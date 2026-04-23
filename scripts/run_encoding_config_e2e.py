"""Local e2e runner for stego encoding profiles.

Generates synthetic posts, runs extractive profile encoding, validates payload
recovery, writes n8n-shaped artifacts, and computes divergence metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import string
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.config import (  # noqa: E402
    get_workflow_encoding_secret,
    get_workflow_encoding_settings,
    get_workflow_payload_transform,
)
from infrastructure.json_logging import configure_api_logging  # noqa: E402
from loguru import logger  # noqa: E402
from services.stego_metrics_service import run_divergence_metrics  # noqa: E402
from workflows.pipelines.gen_angles import GenAnglesPipeline  # noqa: E402
from workflows.pipelines.stego import StegoPipeline  # noqa: E402
from workflows.utils.output_results_shape import n8n_save_object_body  # noqa: E402
from workflows.utils.protocol_utils import stable_hash  # noqa: E402
from workflows.utils.stego_codec import (  # noqa: E402
    extract_invisible_payload,
    strip_invisible_payload,
    unprotect_payload,
)

RUNS_ROOT = _REPO_ROOT / "metrics" / "e2e_runs"
DEFAULT_PROFILES = ("robustness", "capacity", "security")
PROFILE_OVERRIDE_KEYS = (
    "WORKFLOW_ANGLES_GENERATION_MODE",
    "WORKFLOW_STEGO_GENERATION_MODE",
    "WORKFLOW_CAPACITY_PROFILE",
    "WORKFLOW_CAPACITY_LIMITS_ENABLED",
    "WORKFLOW_PAYLOAD_TRANSFORM",
    "WORKFLOW_STEGO_PROMPT_STYLE",
    "WORKFLOW_STEGO_SAMPLE_ANGLE_COUNT",
    "WORKFLOW_STEGO_MAX_RETRIES",
    "WORKFLOW_DECODE_SEMANTIC_TOP_N",
    "WORKFLOW_DECODE_LLM_MAX_TRIES",
    "WORKFLOW_STEGO_LLM_TEMPERATURE",
    "WORKFLOW_DECODE_STRICT_DEFAULT",
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def _profile_env(profile: str) -> Iterator[None]:
    keys = ("WORKFLOW_ENCODING_PROFILE", *PROFILE_OVERRIDE_KEYS, "WORKFLOW_ENCODING_SECRET")
    old_values = {key: os.environ.get(key) for key in keys}
    try:
        for key in PROFILE_OVERRIDE_KEYS:
            os.environ.pop(key, None)
        os.environ["WORKFLOW_ENCODING_PROFILE"] = profile
        if profile == "security":
            os.environ.setdefault(
                "WORKFLOW_ENCODING_SECRET",
                "local-e2e-security-profile-secret",
            )
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _body_for(profile: str, idx: int, slot: int, rng: random.Random) -> str:
    topic = rng.choice(("carrier", "metric", "sample", "workflow", "profile"))
    token = "".join(rng.choice(string.ascii_lowercase) for _ in range(8))
    return (
        f"The {topic} discussion for profile {profile} sample {idx:03d} keeps "
        f"stable wording in comment slot {slot} with marker {token}."
    )


def _build_post(profile: str, idx: int, rng: random.Random) -> dict[str, Any]:
    comments = [
        {
            "id": f"{profile}-{idx:03d}-c{slot}",
            "author": f"user_{slot}",
            "body": _body_for(profile, idx, slot, rng),
            "replies": [],
        }
        for slot in range(3)
    ]
    return {
        "id": f"{profile}-{idx:03d}",
        "title": f"Encoding profile {profile} sample {idx:03d}",
        "selftext": "",
        "url": f"https://example.test/{profile}/{idx:03d}",
        "comments": comments,
        "search_results": [],
    }


def _visible_text(post: dict[str, Any]) -> str:
    return "\n".join(str(comment["body"]) for comment in post["comments"])


def _payload_for(profile: str, idx: int, rng: random.Random, payload_bytes: int) -> str:
    prefix = f"{profile}:{idx:03d}:"
    alphabet = string.ascii_letters + string.digits
    body_len = max(1, payload_bytes - len(prefix.encode("utf-8")))
    return prefix + "".join(rng.choice(alphabet) for _ in range(body_len))


def _decode_embedded_payload(stego_text: str) -> str:
    embedded = extract_invisible_payload(stego_text)
    if embedded is None:
        raise RuntimeError("encoded stego text has no invisible payload")
    payload = unprotect_payload(
        embedded,
        transform=get_workflow_payload_transform(),
        secret=get_workflow_encoding_secret(),
    )
    if payload is None:
        raise RuntimeError("embedded payload could not be decoded with active profile")
    return payload


def _metric_progress(label: str, current: int, total: int) -> None:
    logger.bind(component="EncodingConfigE2E").info(
        "metric_progress",
        label=label,
        current=current,
        total=total,
    )


def _run_profile(
    *,
    profile: str,
    samples: int,
    payload_bytes: int,
    run_dir: Path,
    seed: int,
    max_primary_kl: float,
) -> dict[str, Any]:
    rng = random.Random(seed)
    profile_dir = run_dir / profile
    dataset_dir = profile_dir / "dataset"
    output_dir = profile_dir / "output-results"
    metrics_dir = profile_dir / "metrics"
    gen_angles = GenAnglesPipeline()
    stego = StegoPipeline()
    payload_hashes: set[str] = set()
    visible_hashes: set[str] = set()
    embedded_hashes: set[str] = set()

    settings = get_workflow_encoding_settings()
    logger.bind(component="EncodingConfigE2E").info(
        "profile_start",
        profile=profile,
        samples=samples,
        settings=settings,
    )

    for idx in range(samples):
        post = _build_post(profile, idx, rng)
        payload = _payload_for(profile, idx, rng, payload_bytes)
        angles_post = gen_angles.process_post(post)
        result = stego.encode(payload=payload, post=angles_post, tag=f"version_{profile}")
        stego_text = str(result.get("stego_text", ""))
        decoded_payload = _decode_embedded_payload(stego_text)
        visible_text = strip_invisible_payload(stego_text)
        embedded = extract_invisible_payload(stego_text) or ""
        if not result.get("succeeded") or decoded_payload != payload:
            raise RuntimeError(f"profile {profile} sample {idx} failed payload recovery")
        if visible_text != _visible_text(post):
            raise RuntimeError(f"profile {profile} sample {idx} changed visible text")

        payload_hashes.add(stable_hash(payload))
        visible_hashes.add(stable_hash(visible_text))
        embedded_hashes.add(stable_hash(embedded))
        _write_json(dataset_dir / f"{post['id']}.json", post)
        _write_json(
            output_dir / f"{post['id']}_version_{profile}.json",
            n8n_save_object_body(result),
        )
        if idx == 0 or (idx + 1) % max(1, samples // 20) == 0 or idx + 1 == samples:
            logger.bind(component="EncodingConfigE2E").info(
                "profile_progress",
                profile=profile,
                current=idx + 1,
                total=samples,
                unique_payloads=len(payload_hashes),
                unique_visible_texts=len(visible_hashes),
            )

    divergence = run_divergence_metrics(
        output_dir,
        dataset_dir,
        metrics_dir,
        progress_hook=_metric_progress,
    )
    report = divergence["report"]
    primary_kl = report["primary_baseline_matched_post"][
        "average_kl_stego_vs_matched_post"
    ]
    if primary_kl is None or abs(float(primary_kl)) > max_primary_kl:
        raise RuntimeError(
            f"profile {profile} primary KL {primary_kl} exceeds {max_primary_kl}"
        )
    summary = {
        "profile": profile,
        "settings": settings,
        "samples": samples,
        "payload_bytes": payload_bytes,
        "unique_payloads": len(payload_hashes),
        "unique_visible_texts": len(visible_hashes),
        "unique_embedded_payloads": len(embedded_hashes),
        "metrics_report_path": divergence["report_path"],
        "metrics_report": report,
    }
    _write_json(profile_dir / "summary.json", summary)
    logger.bind(component="EncodingConfigE2E").info(
        "profile_complete",
        profile=profile,
        samples=samples,
        primary_kl=primary_kl,
        unique_payloads=len(payload_hashes),
    )
    return summary


def run_encoding_config_e2e(
    *,
    profiles: Sequence[str],
    samples_per_profile: int,
    payload_bytes: int,
    run_dir: Path,
    overwrite: bool,
    seed: int,
    max_primary_kl: float,
) -> dict[str, Any]:
    resolved_run_dir = run_dir.resolve()
    if resolved_run_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Run directory exists: {resolved_run_dir}")
        shutil.rmtree(resolved_run_dir)
    resolved_run_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    for profile_index, profile in enumerate(profiles):
        with _profile_env(profile):
            summaries.append(
                _run_profile(
                    profile=profile,
                    samples=samples_per_profile,
                    payload_bytes=payload_bytes,
                    run_dir=resolved_run_dir,
                    seed=seed + profile_index,
                    max_primary_kl=max_primary_kl,
                )
            )
    combined = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "run_dir": str(resolved_run_dir),
        "profiles": list(profiles),
        "samples_per_profile": samples_per_profile,
        "payload_bytes": payload_bytes,
        "seed": seed,
        "summaries": summaries,
    }
    _write_json(resolved_run_dir / "summary.json", combined)
    logger.bind(component="EncodingConfigE2E").info(
        "run_complete",
        run_dir=str(resolved_run_dir),
        profiles=list(profiles),
        samples_per_profile=samples_per_profile,
    )
    return combined


def _parse_profiles(raw_profiles: Sequence[str]) -> list[str]:
    if not raw_profiles:
        return list(DEFAULT_PROFILES)
    profiles: list[str] = []
    for raw in raw_profiles:
        profiles.extend(part.strip() for part in raw.split(",") if part.strip())
    return profiles or list(DEFAULT_PROFILES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local e2e checks for encoding profiles.")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--samples-per-profile", type=int, default=200)
    parser.add_argument("--payload-bytes", type=int, default=512)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--max-primary-kl", type=float, default=1e-12)
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_api_logging(level=args.log_level, log_file=None, enable_file_log=False)
    run_dir = (
        Path(args.run_dir)
        if args.run_dir
        else RUNS_ROOT / f"encoding_profiles_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    run_encoding_config_e2e(
        profiles=_parse_profiles(args.profile),
        samples_per_profile=max(1, args.samples_per_profile),
        payload_bytes=max(1, args.payload_bytes),
        run_dir=run_dir,
        overwrite=bool(args.overwrite),
        seed=args.seed,
        max_primary_kl=args.max_primary_kl,
    )


if __name__ == "__main__":
    main()
