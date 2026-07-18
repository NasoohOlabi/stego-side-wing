"""Run the frozen, paired dynamic-capacity publication benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.zlg_comparison_service import (  # noqa: E402
    ComparisonInput,
    append_jsonl,
    run_comparison_frames,
    run_comparison_sample,
    stegotext_has_prompt_leakage,
)
from workflows.pipelines.receiver import ReceiverPipeline  # noqa: E402
from workflows.pipelines.stego import StegoPipeline  # noqa: E402
from workflows.utils.stego_codec import (  # noqa: E402
    flatten_comments,
    selection_channel_capacity_report,
)

LOG = logger.bind(component="PublicationBenchmark")
METHODS = ("our_method", "official_zgls")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_dirty() -> bool:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def _runtime_code_sha256() -> str:
    paths = [Path(__file__).resolve()]
    paths.extend(
        path
        for path in sorted(SRC.rglob("*.py"))
        if "tests" not in path.relative_to(SRC).parts
    )
    paths.extend(sorted((ROOT / "config").rglob("*.json")))
    paths.extend(sorted((ROOT / "workflows").rglob("*.json")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _zlg_server_identity(
    server_url: str, declared_server_version: str = ""
) -> dict[str, Any]:
    if server_url.startswith("local://"):
        return {
            "backend": "local_hf",
            "model": server_url.removeprefix("local://"),
            "server_version": _runtime_code_sha256(),
        }
    response = requests.get(f"{server_url.rstrip('/')}/health", timeout=30)
    response.raise_for_status()
    identity = response.json()
    if not isinstance(identity, dict) or identity.get("status") != "ok" or not identity.get(
        "model"
    ):
        raise ValueError("ZLG /health must report status=ok and the loaded model")
    version = (
        identity.get("server_version")
        or identity.get("implementation_sha256")
        or declared_server_version.strip()
    )
    if not version:
        raise ValueError(
            "ZLG server version is missing; report it in /health or pass --zlg-server-version"
        )
    identity["benchmark_server_version"] = str(version)
    return identity


def _verify_manifest(
    manifest: dict[str, Any], angles_dir: Path, *, allow_dirty: bool = False
) -> list[str]:
    post_ids = [str(value) for value in manifest.get("post_ids", [])]
    if len(post_ids) < 100 or len(post_ids) != len(set(post_ids)):
        raise ValueError("Manifest must contain at least 100 unique post IDs")
    if manifest.get("git_commit") != _git_commit():
        raise ValueError("Manifest Git commit does not match the running code")
    if not allow_dirty and (manifest.get("git_dirty") or _git_dirty()):
        raise ValueError("Publication benchmark requires a clean manifest and working tree")
    hashes = manifest.get("angle_artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(post_ids):
        raise ValueError("Manifest does not hash every frozen angle artifact")
    for post_id in post_ids:
        if _sha256(angles_dir / f"{post_id}.json") != hashes[post_id]:
            raise ValueError(f"Frozen angle artifact changed: {post_id}")
    expected = {row["post_id"]: row for row in manifest.get("payload_assignments", [])}
    if any(expected.get(post_id, {}).get("payload_sha256") != hashlib.sha256(_payload(post_id, int(expected.get(post_id, {}).get("seed", -1))).encode()).hexdigest() for post_id in post_ids):
        raise ValueError("Manifest payload assignment is incomplete or inconsistent")
    return post_ids


def _payload(post_id: str, seed: int) -> str:
    return hashlib.sha256(f"{post_id}:{seed}".encode()).hexdigest()[:8]


def _run_signature(manifest: dict[str, Any], args: argparse.Namespace) -> str:
    identity = {
        "manifest": manifest,
        "comparison_mode": args.comparison_mode,
        "zlg_server_url": args.zlg_server_url,
        "max_carriers": args.max_carriers,
        "max_total_words": args.max_total_words,
        "max_retries": args.max_retries,
        "zlg_max_new_tokens": args.zlg_max_new_tokens,
        "allow_dirty": args.allow_dirty,
        "runtime_code_sha256": _runtime_code_sha256(),
        "zlg_server_identity": args.zlg_server_identity,
    }
    encoded = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE))


def _cover_texts(post: dict[str, Any]) -> list[str]:
    bodies = [str(row.get("body") or "").strip() for row in flatten_comments(post.get("comments", []))]
    suitable = [body for body in bodies if 4 <= _word_count(body) <= 60]
    fallback = [str(post.get("title") or ""), str(post.get("selftext") or "")]
    return (suitable or [text for text in fallback if text.strip()])[:32]


def _frame_texts(encoded: dict[str, Any]) -> list[str]:
    return [str(frame.get("stego_text") or "") for frame in encoded.get("frames", [])]


def _our_accounting(
    encoded: dict[str, Any], recovered: bool, payload_bits_target: int
) -> dict[str, Any]:
    meta = encoded.get("recovery_meta") or {}
    control = int(meta.get("control_bit_length") or 0)
    transformed = int(meta.get("payload_bit_length") or 0)
    return {
        "payload_bits_target": payload_bits_target,
        "payload_bits_encoded": payload_bits_target if recovered else 0,
        "transformed_payload_bits": transformed,
        "protocol_overhead_bits": control,
        "total_embedded_bits": transformed + control,
    }


def _run_our_method(
    post: dict[str, Any],
    payload: str,
    max_carriers: int,
    max_total_words: int,
    max_retries: int,
) -> dict[str, Any]:
    encoded = StegoPipeline().encode_payload_frames(
        payload,
        [post],
        max_frames_per_post=max_carriers,
        max_retries=max(0, max_retries),
    )
    decoded = {"succeeded": False, "payload": None}
    if encoded.get("succeeded"):
        decoded = ReceiverPipeline().run_multi_frame(
            encoded["posts"],
            "sender",
            ordered_frame_refs=encoded["ordered_frame_refs"],
            payload_transform="plain",
        )
    recovered = bool(decoded.get("succeeded") and decoded.get("payload") == payload)
    texts = _frame_texts(encoded)
    word_count = sum(_word_count(text) for text in texts)
    within_budget = word_count <= max_total_words
    compact_frames = [
        {
            key: frame.get(key)
            for key in (
                "frame_index",
                "capacity",
                "capacity_report",
                "bits_used",
                "padding_bits",
                "post_id",
                "comment_id",
                "stego_text",
            )
        }
        for frame in encoded.get("frames", [])
    ]
    return {
        "accepted": bool(encoded.get("succeeded") and recovered and within_budget),
        "decode_ok": recovered,
        "reason": (
            "generated_word_budget_exhausted"
            if recovered and not within_budget
            else (encoded.get("error") if not recovered else None)
        ),
        "carrier_count": len(encoded.get("frames", [])),
        "word_count": word_count,
        "stegotexts": texts,
        "frames": compact_frames,
        "_receiver_artifact": {
            "target_payload": payload,
            "posts": encoded.get("posts", []),
            "ordered_frame_refs": encoded.get("ordered_frame_refs", []),
            "payload_transform": "plain",
        },
        **_our_accounting(encoded, recovered, len(payload.encode("utf-8")) * 8),
    }


def _deterministic_capacity_payload(post_id: str, seed: int, byte_count: int) -> str:
    source = f"{post_id}:{seed}:max-capacity".encode()
    return hashlib.shake_256(source).hexdigest((byte_count + 1) // 2)[:byte_count]


def _our_capacity_payloads(
    post: dict[str, Any], post_id: str, seed: int, max_carriers: int
) -> list[str]:
    report = selection_channel_capacity_report(post)
    upper_bytes = int(report["recoverable_capacity_bits"]) * max(0, max_carriers) // 8
    pipeline = StegoPipeline()
    payloads: list[str] = []
    for byte_count in range(upper_bytes, 0, -1):
        payload = _deterministic_capacity_payload(post_id, seed, byte_count)
        plan = pipeline.plan_payload_frames(
            payload, [post], max_frames_per_post=max_carriers
        )
        if plan.get("succeeded"):
            payloads.append(payload)
    return payloads


def _max_our_payload(
    post: dict[str, Any], post_id: str, seed: int, max_carriers: int
) -> str:
    payloads = _our_capacity_payloads(post, post_id, seed, max_carriers)
    return payloads[0] if payloads else ""


def _run_our_max_capacity(
    post: dict[str, Any],
    post_id: str,
    seed: int,
    max_carriers: int,
    max_total_words: int,
    max_retries: int,
) -> tuple[dict[str, Any], str]:
    payloads = _our_capacity_payloads(post, post_id, seed, max_carriers)
    trials: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None
    last_payload = ""
    for payload in payloads:
        result = _run_our_method(
            post, payload, max_carriers, max_total_words, max_retries
        )
        trials.append(
            {
                "payload_bits": len(payload.encode("utf-8")) * 8,
                "accepted": bool(result.get("accepted")),
                "decode_ok": bool(result.get("decode_ok")),
                "carrier_count": int(result.get("carrier_count") or 0),
                "word_count": int(result.get("word_count") or 0),
                "reason": result.get("reason"),
            }
        )
        last_result, last_payload = result, payload
        if result.get("accepted"):
            result["capacity_trials"] = trials
            result["capacity_probe_ceiling_bits"] = (
                len(payloads[0].encode("utf-8")) * 8
            )
            result["capacity_censored"] = False
            return result, payload
    if last_result is None:
        last_result = {
            "accepted": False,
            "decode_ok": False,
            "reason": "No useful payload fits the dynamic frame budget",
            "payload_bits_encoded": 0,
            "protocol_overhead_bits": 0,
            "total_embedded_bits": 0,
            "carrier_count": 0,
            "word_count": 0,
            "stegotexts": [],
            "frames": [],
        }
    last_result["capacity_trials"] = trials
    last_result["capacity_probe_ceiling_bits"] = (
        len(payloads[0].encode("utf-8")) * 8 if payloads else 0
    )
    last_result["capacity_censored"] = False
    return last_result, last_payload


def _candidate_int(
    source: dict[str, Any], names: tuple[str, ...], fallback: int = 0
) -> int:
    for name in names:
        value = source.get(name)
        if isinstance(value, (int, float)):
            return int(value)
    return fallback


def _capacity_frame_from_source(
    source: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    stegotext = str(source.get("stegotext") or result.get("stegotext") or "")
    payload_segment = source.get("secret") or source.get("target_payload")
    useful = _candidate_int(
        source,
        ("payload_bits_exact", "payload_bits", "payload_bits_encoded"),
        int(result.get("payload_bits_encoded") or 0),
    )
    overhead = _candidate_int(
        source, ("header_bits", "protocol_overhead_bits"), int(result.get("protocol_overhead_bits") or 0)
    )
    total = _candidate_int(
        source,
        ("total_used_bits", "used_bits", "total_embedded_bits"),
        int(result.get("total_embedded_bits") or 0),
    )
    decode_value = (
        source.get("decode_ok") if "decode_ok" in source else result.get("decode_ok")
    )
    return {
        **source,
        "stegotext": stegotext,
        "decode_ok": bool(decode_value),
        "payload_segment": payload_segment if isinstance(payload_segment, str) else None,
        "payload_bits_encoded": useful,
        "protocol_overhead_bits": overhead,
        "total_embedded_bits": total,
        "word_count": _word_count(stegotext),
    }


def _capacity_frame(result: dict[str, Any], max_words: int) -> dict[str, Any] | None:
    raw_trials = result.get("capacity_trials")
    sources = [row for row in raw_trials if isinstance(row, dict)] if isinstance(raw_trials, list) else []
    best = result.get("capacity_best_success")
    if isinstance(best, dict):
        sources.append(best)
    sources.append(result)
    candidates: list[dict[str, Any]] = []
    for source in sources:
        if source is result and not result.get("accepted"):
            continue
        if "success" in source and not source.get("success"):
            continue
        if source.get("quality_passed") is False:
            continue
        frame = _capacity_frame_from_source(source, result)
        if (
            frame["stegotext"]
            and frame["decode_ok"]
            and frame["word_count"] <= max_words
            and not stegotext_has_prompt_leakage(frame["stegotext"])
        ):
            candidates.append(frame)
    return max(candidates, key=lambda frame: int(frame["payload_bits_encoded"]), default=None)


def _run_zlg_max_capacity(
    sample: ComparisonInput, *, max_carriers: int, max_total_words: int
) -> dict[str, Any]:
    frames: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    total_words = 0
    total_latency = 0
    for carrier_index in range(max(0, max_carriers)):
        remaining_words = max_total_words - total_words
        if remaining_words <= 0:
            break
        probe = run_comparison_sample(
            replace(
                sample,
                seed=sample.seed + carrier_index,
                quality_max_words=max(1, remaining_words),
                max_new_tokens=min(sample.max_new_tokens, max(1, remaining_words * 2)),
                use_capacity_probe=True,
            )
        )
        total_latency += int(probe.get("latency_ms") or 0)
        frame = _capacity_frame(probe, remaining_words)
        probes.append(probe)
        if frame is None:
            break
        frame["carrier_index"] = carrier_index
        frames.append(frame)
        total_words += int(frame["word_count"])
    useful = sum(int(frame["payload_bits_encoded"]) for frame in frames)
    overhead = sum(int(frame["protocol_overhead_bits"]) for frame in frames)
    total = sum(int(frame["total_embedded_bits"]) for frame in frames)
    ceiling = max(sample.payload_bits_candidates, default=0)
    accepted = bool(frames) and all(frame["decode_ok"] for frame in frames)
    return {
        "accepted": accepted,
        "decode_ok": accepted,
        "reason": None if accepted else "capacity_probe_no_verified_carrier",
        "frames": frames,
        "carrier_count": len(frames),
        "word_count": total_words,
        "payload_bits_target": useful,
        "payload_bits_encoded": useful,
        "protocol_overhead_bits": overhead,
        "total_embedded_bits": total,
        "capacity_probe_ceiling_bits": ceiling * max(0, max_carriers),
        "capacity_probe_ceiling_bits_per_carrier": ceiling,
        "capacity_censored": any(
            int(frame["payload_bits_encoded"]) >= ceiling for frame in frames
        ),
        "capacity_probes": probes,
        "latency_ms": total_latency,
    }


def _run_zlg(post: dict[str, Any], payload: str, args: argparse.Namespace, seed: int) -> dict[str, Any]:
    sample = ComparisonInput(
        target_payload=payload,
        server_url=args.zlg_server_url,
        cover_texts=_cover_texts(post),
        seed=seed,
        max_retries=args.max_retries,
        max_new_tokens=max(1, args.zlg_max_new_tokens),
        quality_max_words=max(1, args.max_total_words),
        quality_max_retries=max(1, args.max_retries),
        use_capacity_probe=args.comparison_mode == "max_capacity",
    )
    if args.comparison_mode == "max_capacity":
        result = _run_zlg_max_capacity(
            sample,
            max_carriers=args.max_carriers,
            max_total_words=args.max_total_words,
        )
    else:
        result = run_comparison_frames(
            sample, max_carriers=args.max_carriers, max_total_words=args.max_total_words
        )
    result["stegotexts"] = [str(frame.get("stegotext") or "") for frame in result["frames"]]
    result["_receiver_artifact"] = {
        "server_url": args.zlg_server_url,
        "target_payload": payload if args.comparison_mode == "capacity_matched" else None,
        "comparison_mode": args.comparison_mode,
        "frames": result["frames"],
    }
    result["frames"] = [
        {
            key: frame.get(key)
            for key in (
                "payload_bits_encoded",
                "protocol_overhead_bits",
                "total_embedded_bits",
                "word_count",
                "stegotext",
                "decode_ok",
            )
        }
        for frame in result["frames"]
    ]
    return result


def _failure(method: str, exc: Exception) -> dict[str, Any]:
    return {
        "method": method,
        "accepted": False,
        "decode_ok": False,
        "reason": f"{type(exc).__name__}: {exc}",
        "payload_bits_encoded": 0,
    }


def _load_done(path: Path, run_signature: str) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {
        (str(row.get("post_id")), str(row.get("method")))
        for row in rows
        if row.get("run_signature") == run_signature
    }


def _run_tuple(
    post_id: str, method: str, post: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    seed = int(hashlib.sha256(post_id.encode()).hexdigest()[:8], 16)
    payload = _payload(post_id, seed)
    method_payload = payload
    started = time.perf_counter()
    try:
        if method == "our_method" and args.comparison_mode == "max_capacity":
            result, method_payload = _run_our_max_capacity(
                post,
                post_id,
                seed,
                args.max_carriers,
                args.max_total_words,
                args.max_retries,
            )
        elif method == "our_method":
            result = _run_our_method(
                post, payload, args.max_carriers, args.max_total_words, args.max_retries
            )
        else:
            result = _run_zlg(post, payload, args, seed)
    except Exception as exc:
        LOG.bind(trace_id=uuid4().hex, post_id=post_id, method=method).exception(
            "benchmark_tuple_failed"
        )
        result = _failure(method, exc)
    artifact = result.pop("_receiver_artifact", None)
    artifact_path = None
    if isinstance(artifact, dict):
        artifact_path = Path(args.run_dir).resolve() / "receiver_artifacts" / post_id / f"{method}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    return {
        "post_id": post_id,
        "method": method,
        "comparison_mode": args.comparison_mode,
        "run_signature": args.run_signature,
        "zlg_server_identity_sha256": hashlib.sha256(
            json.dumps(
                args.zlg_server_identity,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "human_texts": _cover_texts(post)[:8],
        "payload_hash": (
            hashlib.sha256(method_payload.encode()).hexdigest()
            if method_payload
            and (method == "our_method" or args.comparison_mode == "capacity_matched")
            else None
        ),
        "payload_bits_target": len(method_payload.encode("utf-8")) * 8,
        "seed": seed,
        "attempted_at_utc": datetime.now(UTC).isoformat(),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "receiver_artifact": str(artifact_path) if artifact_path else None,
        **result,
    }


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _rows_for_posts(rows: list[dict[str, Any]], post_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(post_ids)
    return [row for row in rows if str(row.get("post_id")) in selected]


def _rows_for_signature(rows: list[dict[str, Any]], run_signature: str) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("run_signature") == run_signature]


def _accounting_valid(row: dict[str, Any]) -> bool:
    useful = int(row.get("payload_bits_encoded") or 0)
    overhead = int(row.get("protocol_overhead_bits") or 0)
    total = int(row.get("total_embedded_bits") or 0)
    if min(useful, overhead, total) < 0:
        return False
    if row.get("method") == "our_method":
        transformed = int(row.get("transformed_payload_bits") or 0)
        return total == transformed + overhead
    return total >= useful and total == useful + overhead


def _summary(rows: list[dict[str, Any]], requested_posts: int) -> dict[str, Any]:
    counts = Counter(str(row.get("method")) for row in rows)
    methods: dict[str, Any] = {}
    for method in METHODS:
        group = [row for row in rows if row.get("method") == method]
        accepted = sum(bool(row.get("accepted")) for row in group)
        recovered = sum(bool(row.get("accepted") and row.get("decode_ok")) for row in group)
        methods[method] = {
            "attempted": len(group),
            "accepted": accepted,
            "failed": len(group) - accepted,
            "generation_success_rate": accepted / len(group) if group else 0.0,
            "verified_recovery_rate": recovered / accepted if accepted else 0.0,
        }
    complete = all(counts[method] == requested_posts for method in METHODS)
    accounting_ok = all(_accounting_valid(row) for row in rows)
    gate = complete and accounting_ok and all(
        block["generation_success_rate"] >= 0.80
        and block["verified_recovery_rate"] >= 0.95
        for block in methods.values()
    )
    return {
        "requested_posts": requested_posts,
        "methods": methods,
        "complete": complete,
        "accounting_invariants_passed": accounting_ok,
        "expansion_gate_passed": gate,
    }


def _run_posts(post_ids: list[str], args: argparse.Namespace, results: Path) -> None:
    done = _load_done(results, args.run_signature)
    for post_id in post_ids:
        post = _read_json(Path(args.angles_dir) / f"{post_id}.json")
        for method in METHODS:
            if (post_id, method) in done:
                continue
            append_jsonl(results, _run_tuple(post_id, method, post, args))


def _write_summary(
    path: Path, rows: list[dict[str, Any]], count: int, run_signature: str
) -> dict[str, Any]:
    summary = {"run_signature": run_signature, **_summary(rows, count)}
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--angles-dir", default="metrics/benchmark/prepared_angles")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--stage", choices=("pilot", "full", "auto"), default="auto")
    parser.add_argument("--comparison-mode", choices=("capacity_matched", "max_capacity"), default="capacity_matched")
    parser.add_argument("--zlg-server-url", default="http://127.0.0.1:9000")
    parser.add_argument(
        "--zlg-server-version",
        default="",
        help="Deployed ZLG server commit or image digest when /health omits it",
    )
    parser.add_argument("--max-carriers", type=int, default=8)
    parser.add_argument("--max-total-words", type=int, default=320)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--zlg-max-new-tokens", type=int, default=640)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    manifest = _read_json(Path(args.manifest).resolve())
    post_ids = _verify_manifest(
        manifest, Path(args.angles_dir).resolve(), allow_dirty=args.allow_dirty
    )
    args.zlg_server_identity = _zlg_server_identity(
        args.zlg_server_url, args.zlg_server_version
    )
    args.run_signature = _run_signature(manifest, args)
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "zlg_server_identity.json").write_text(
        json.dumps(args.zlg_server_identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    results = run_dir / "attempts.jsonl"
    pilot_ids = post_ids[:25]
    if args.stage == "full":
        pilot_path = run_dir / "pilot_summary.json"
        pilot = _read_json(pilot_path) if pilot_path.exists() else {}
        if pilot.get("run_signature") != args.run_signature or not pilot.get(
            "expansion_gate_passed"
        ):
            raise SystemExit("Full stage requires a passing pilot_summary.json in the same run directory")
    if args.stage in {"pilot", "auto"}:
        _run_posts(pilot_ids, args, results)
        current_rows = _rows_for_signature(_rows(results), args.run_signature)
        pilot_rows = _rows_for_posts(current_rows, pilot_ids)
        pilot = _write_summary(
            run_dir / "pilot_summary.json", pilot_rows, 25, args.run_signature
        )
        if args.stage == "pilot" or not pilot["expansion_gate_passed"]:
            return 0 if pilot["expansion_gate_passed"] else 2
    _run_posts(post_ids, args, results)
    current_rows = _rows_for_signature(_rows(results), args.run_signature)
    final = _write_summary(run_dir / "summary.json", current_rows, 100, args.run_signature)
    return 0 if final["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
