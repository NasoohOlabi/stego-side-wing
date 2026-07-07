"""Claims-led statistical reporting for stego experiment artifacts."""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from infrastructure.config import REPO_ROOT

DEFAULT_REPORT_MD = REPO_ROOT / "metrics" / "CLAIMS_REPORT.md"
DEFAULT_REPORT_JSON = REPO_ROOT / "metrics" / "CLAIMS_REPORT.json"
DEFAULT_MANIFEST = REPO_ROOT / "config" / "claims_manifest.json"


@dataclass
class VariantEvidence:
    name: str
    successful_samples: int
    failed_samples: int
    post_ids: list[str] = field(default_factory=list)
    failure_classes: Counter[str] = field(default_factory=Counter)
    receiver_successes: int = 0
    semantic_angle_successes: int = 0
    audit_assisted_recoveries: int = 0
    pure_selection_recoveries: int = 0
    context_drift_failures: int = 0
    secure_transform_successes: int = 0
    secure_transform_total: int = 0
    hidden_payload_bytes: list[float] = field(default_factory=list)
    bps_selection: list[float] = field(default_factory=list)
    matched_post_jsd: list[float] = field(default_factory=list)
    matched_post_kl: list[float] = field(default_factory=list)
    perplexity: list[float] = field(default_factory=list)
    per_post_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    aggregate_metrics: dict[str, Any] = field(default_factory=dict)


def wilson_ci(successes: int, total: int, *, z: float = 1.959963984540054) -> dict[str, Any]:
    if total <= 0:
        return {"n": total, "successes": successes, "lower": None, "upper": None}
    p_hat = successes / total
    denom = 1 + (z * z / total)
    centre = p_hat + (z * z / (2 * total))
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * total)) / total)
    return {
        "n": total,
        "successes": successes,
        "lower": max(0.0, (centre - margin) / denom),
        "upper": min(1.0, (centre + margin) / denom),
    }


def bootstrap_mean_ci(
    values: list[float],
    *,
    iterations: int = 1000,
    seed: int = 1337,
    confidence: float = 0.95,
) -> dict[str, Any]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {"n": 0, "mean": None, "lower": None, "upper": None}
    if len(clean) == 1:
        return {"n": 1, "mean": clean[0], "lower": clean[0], "upper": clean[0]}
    rng = random.Random(seed)
    n = len(clean)
    means = []
    for _ in range(iterations):
        sample = [clean[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1 - confidence) / 2
    lo_idx = max(0, min(len(means) - 1, int(alpha * len(means))))
    hi_idx = max(0, min(len(means) - 1, int((1 - alpha) * len(means)) - 1))
    return {"n": n, "mean": sum(clean) / n, "lower": means[lo_idx], "upper": means[hi_idx]}


def paired_bootstrap_delta_ci(
    baseline: list[float],
    candidate: list[float],
    *,
    iterations: int = 1000,
    seed: int = 1337,
    confidence: float = 0.95,
) -> dict[str, Any]:
    pairs = [
        (float(a), float(b))
        for a, b in zip(baseline, candidate, strict=False)
        if math.isfinite(float(a)) and math.isfinite(float(b))
    ]
    if not pairs:
        return {"n": 0, "mean_delta": None, "lower": None, "upper": None}
    deltas = [b - a for a, b in pairs]
    ci = bootstrap_mean_ci(deltas, iterations=iterations, seed=seed, confidence=confidence)
    ci["mean_delta"] = ci.pop("mean")
    return ci


def load_claims_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_summary_artifacts(paths: list[Path]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in paths:
        if path.is_dir():
            candidates = sorted(path.glob("summary.json"))
            candidates.extend(sorted(path.glob("*/summary.json")))
        else:
            candidates = [path]
        for candidate in candidates:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload["_artifact_path"] = str(candidate.resolve())
                progress = candidate.with_name("progress.json")
                if progress.is_file():
                    progress_payload = json.loads(progress.read_text(encoding="utf-8"))
                    if isinstance(progress_payload, dict):
                        payload["_progress"] = progress_payload
                artifacts.append(payload)
    return artifacts


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _collect_float(entry: dict[str, Any], group: str, key: str) -> float | None:
    for root_key in ("sample_metrics", "metrics"):
        root = entry.get(root_key)
        if isinstance(root, dict):
            group_value = root.get(group)
            if isinstance(group_value, dict):
                found = _as_float(group_value.get(key))
                if found is not None:
                    return found
    group_value = entry.get(group)
    if isinstance(group_value, dict):
        return _as_float(group_value.get(key))
    return None


def _quality_series(entry: dict[str, Any], key: str) -> float | None:
    return _collect_float(entry, "quality_metrics", key)


def _recovery_source(receiver_decode: dict[str, Any]) -> str:
    meta = receiver_decode.get("recovery_meta")
    if not isinstance(meta, dict):
        return "unknown"
    if meta.get("used_compressed_full") is False:
        return "pure_selection_channel"
    source = meta.get("recovery_source")
    if isinstance(source, str):
        return source
    if meta.get("used_compressed_full") is True:
        return "audit_assisted_compressed_full"
    return "unknown"


def _variant_from_lane(lane: dict[str, Any], fallback_post_ids: list[str]) -> VariantEvidence:
    entries = [entry for entry in lane.get("entries", []) if isinstance(entry, dict)]
    failures = [failure for failure in lane.get("failures", []) if isinstance(failure, dict)]
    name = str(lane.get("variant") or lane.get("profile") or "unknown")
    evidence = VariantEvidence(
        name=name,
        successful_samples=int(lane.get("samples_succeeded") or lane.get("samples") or len(entries)),
        failed_samples=int(lane.get("samples_failed") or len(failures)),
        post_ids=[str(entry.get("post_id")) for entry in entries if entry.get("post_id") is not None]
        or list(fallback_post_ids),
        aggregate_metrics=lane.get("summary_metrics") if isinstance(lane.get("summary_metrics"), dict) else {},
    )
    for failure in failures:
        evidence.failure_classes[str(failure.get("failure_code") or "unclassified_failure")] += 1
        if failure.get("stage") == "context_drift" or failure.get("context_drift"):
            evidence.context_drift_failures += 1
    for entry in entries:
        context_drift = entry.get("context_drift")
        if entry.get("context_drift_failure") or (
            isinstance(context_drift, dict) and context_drift.get("mismatches")
        ):
            evidence.context_drift_failures += 1
        metrics = entry.get("sample_metrics") if isinstance(entry.get("sample_metrics"), dict) else {}
        if bool(metrics.get("receiver_success")) or isinstance(entry.get("receiver_decode"), dict):
            evidence.receiver_successes += 1
        receiver_decode = entry.get("receiver_decode")
        if isinstance(receiver_decode, dict):
            if isinstance(receiver_decode.get("decoded_angle_index"), int):
                evidence.semantic_angle_successes += 1
            source = _recovery_source(receiver_decode)
            if source == "pure_selection_channel":
                evidence.pure_selection_recoveries += 1
            else:
                evidence.audit_assisted_recoveries += 1
            meta = receiver_decode.get("recovery_meta")
            if isinstance(meta, dict):
                transform = str(meta.get("payload_transform") or "")
                if transform in {"hmac_xor_v1", "secure_compact_v2"}:
                    evidence.secure_transform_total += 1
                    evidence.secure_transform_successes += 1
        for dest, group, key in (
            (evidence.hidden_payload_bytes, "carrier_metrics", "hidden_payload_bytes"),
            (evidence.bps_selection, "capacity_metrics", "bps_selection"),
            (evidence.matched_post_jsd, "quality_metrics", "matched_post_jsd"),
            (evidence.matched_post_kl, "quality_metrics", "matched_post_kl"),
            (evidence.perplexity, "quality_metrics", "perplexity"),
        ):
            value = _collect_float(entry, group, key)
            if value is not None:
                dest.append(value)
        post_id = entry.get("post_id")
        if isinstance(post_id, str):
            evidence.per_post_metrics[post_id] = {
                key: value
                for key in ("matched_post_jsd", "matched_post_kl", "perplexity")
                if (value := _quality_series(entry, key)) is not None
            }
    return evidence


def collect_variant_evidence(artifacts: list[dict[str, Any]]) -> list[VariantEvidence]:
    variants: list[VariantEvidence] = []
    for artifact in artifacts:
        fallback_post_ids = [str(pid) for pid in artifact.get("selected_post_ids", [])]
        lanes = artifact.get("profile_summaries") or artifact.get("summaries") or []
        if isinstance(lanes, list):
            variants.extend(_variant_from_lane(lane, fallback_post_ids) for lane in lanes if isinstance(lane, dict))
    return variants


def _provenance_status(artifacts: list[dict[str, Any]], *, accept_historical: bool) -> dict[str, Any]:
    if accept_historical:
        return {"accepted": True, "clean": True, "reason": "historical evidence explicitly accepted"}
    clean_values = []
    commits = []
    for artifact in artifacts:
        progress = artifact.get("_progress") if isinstance(artifact.get("_progress"), dict) else {}
        provenance = artifact.get("provenance") if isinstance(artifact.get("provenance"), dict) else {}
        for source in (artifact, progress, provenance):
            if "git_status_clean" in source:
                clean_values.append(bool(source.get("git_status_clean")))
            if source.get("git_commit"):
                commits.append(str(source.get("git_commit")))
    if clean_values and all(clean_values) and commits:
        return {"accepted": True, "clean": True, "git_commits": sorted(set(commits))}
    return {"accepted": False, "clean": False, "reason": "dirty or unknown run provenance"}


def _sample_gate(variants: list[VariantEvidence], min_samples: int) -> dict[str, Any]:
    too_small = [v.name for v in variants if v.successful_samples < min_samples]
    return {"passed": not too_small and bool(variants), "min_samples": min_samples, "too_small": too_small}


def _same_post_gate(variants: list[VariantEvidence]) -> dict[str, Any]:
    if len(variants) < 2:
        return {"passed": True, "reason": "single variant"}
    first = variants[0].post_ids
    passed = bool(first) and all(v.post_ids == first for v in variants[1:])
    return {"passed": passed, "post_count": len(first), "reason": None if passed else "post ID lists differ"}


def _status_from_min_ci(ci: dict[str, Any], threshold: float) -> str:
    lower = ci.get("lower")
    upper = ci.get("upper")
    if lower is not None and lower >= threshold:
        return "supported"
    if upper is not None and upper < threshold:
        return "not_supported"
    return "inconclusive"


def _status_from_max_ci(ci: dict[str, Any], threshold: float) -> str:
    lower = ci.get("lower")
    upper = ci.get("upper")
    if upper is not None and upper <= threshold:
        return "supported"
    if lower is not None and lower > threshold:
        return "not_supported"
    return "inconclusive"


def _apply_gates(status: str, gates: dict[str, Any]) -> str:
    if not gates["provenance"]["accepted"] or not gates["samples"]["passed"]:
        return "inconclusive"
    return status


def _claim_receiver(variants: list[VariantEvidence], gates: dict[str, Any]) -> dict[str, Any]:
    successes = sum(v.receiver_successes for v in variants)
    total = sum(v.successful_samples + v.failed_samples for v in variants)
    ci = wilson_ci(successes, total)
    return {
        "id": "receiver_recovery_reliability",
        "status": _apply_gates(_status_from_min_ci(ci, 0.95), gates),
        "metric": {"receiver_success_rate_ci": ci},
    }


def _claim_no_invisible(variants: list[VariantEvidence], gates: dict[str, Any]) -> dict[str, Any]:
    values = [value for variant in variants for value in variant.hidden_payload_bytes]
    zero_count = sum(1 for value in values if value == 0)
    ci = wilson_ci(zero_count, len(values))
    raw_status = "not_supported" if any(value > 0 for value in values) else _status_from_min_ci(ci, 0.99)
    return {
        "id": "no_invisible_payload_carrier",
        "status": _apply_gates(raw_status, gates),
        "metric": {"zero_hidden_payload_rate_ci": ci, "hidden_payload_samples": len(values)},
    }


def _claim_capacity(variants: list[VariantEvidence], gates: dict[str, Any]) -> dict[str, Any]:
    values = [value for variant in variants for value in variant.bps_selection]
    ci = bootstrap_mean_ci(values)
    return {
        "id": "selection_channel_capacity_accounting",
        "status": _apply_gates(_status_from_min_ci(ci, 0.0), gates),
        "metric": {"bps_selection_mean_ci": ci},
    }


def _claim_naturalness(variants: list[VariantEvidence], gates: dict[str, Any]) -> dict[str, Any]:
    jsd_values = [value for variant in variants for value in variant.matched_post_jsd]
    ppl_values = [value for variant in variants for value in variant.perplexity]
    jsd_ci = bootstrap_mean_ci(jsd_values)
    status = _status_from_max_ci(jsd_ci, 0.25)
    return {
        "id": "naturalness_stealth",
        "status": _apply_gates(status, gates),
        "metric": {
            "matched_post_jsd_ci": jsd_ci,
            "perplexity_ci": bootstrap_mean_ci(ppl_values),
            "kld_smoothing_alpha_required": True,
            "headline_divergence": "JSD",
        },
    }


def _claim_secure(variants: list[VariantEvidence], gates: dict[str, Any]) -> dict[str, Any]:
    successes = sum(v.secure_transform_successes for v in variants)
    total = sum(v.secure_transform_total for v in variants)
    ci = wilson_ci(successes, total)
    return {
        "id": "secure_payload_transform",
        "status": _apply_gates(_status_from_min_ci(ci, 0.95), gates),
        "metric": {"secure_receiver_success_rate_ci": ci},
    }


def _paired_deltas(variants: list[VariantEvidence]) -> list[dict[str, Any]]:
    if len(variants) < 2:
        return []
    baseline = variants[0]
    rows: list[dict[str, Any]] = []
    for candidate in variants[1:]:
        shared = [pid for pid in baseline.post_ids if pid in candidate.per_post_metrics]
        base_jsd = [baseline.per_post_metrics.get(pid, {}).get("matched_post_jsd") for pid in shared]
        cand_jsd = [candidate.per_post_metrics.get(pid, {}).get("matched_post_jsd") for pid in shared]
        base_clean = [value for value in base_jsd if value is not None]
        cand_clean = [value for value in cand_jsd if value is not None]
        rows.append(
            {
                "baseline": baseline.name,
                "candidate": candidate.name,
                "matched_post_jsd_delta_ci": paired_bootstrap_delta_ci(base_clean, cand_clean),
            }
        )
    return rows


def _claim_comparisons(variants: list[VariantEvidence], gates: dict[str, Any]) -> dict[str, Any]:
    rows = _paired_deltas(variants)
    raw_status = "supported" if rows and gates["same_posts"]["passed"] else "inconclusive"
    return {
        "id": "variant_comparisons_pareto",
        "status": _apply_gates(raw_status, gates),
        "metric": {"same_post_gate": gates["same_posts"], "paired_deltas": rows},
    }


def _claim_receiver_modes(variants: list[VariantEvidence], gates: dict[str, Any]) -> dict[str, Any]:
    mode_rows = [
        {"variant": v.name, "context_drift_failures": v.context_drift_failures}
        for v in variants
        if "snapshot" in v.name or "volatile" in v.name
    ]
    return {
        "id": "ephemeral_snapshot_vs_volatile_receiver",
        "status": _apply_gates("supported" if mode_rows else "inconclusive", gates),
        "metric": {"receiver_modes": mode_rows},
    }


def evaluate_claims(
    artifacts: list[dict[str, Any]],
    *,
    accept_historical: bool = False,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    manifest = load_claims_manifest(manifest_path)
    variants = collect_variant_evidence(artifacts)
    gates = {
        "provenance": _provenance_status(artifacts, accept_historical=accept_historical),
        "samples": _sample_gate(variants, int(manifest["min_successful_samples_per_variant"])),
        "same_posts": _same_post_gate(variants),
    }
    claims = [
        _claim_receiver(variants, gates),
        _claim_no_invisible(variants, gates),
        _claim_capacity(variants, gates),
        _claim_naturalness(variants, gates),
        _claim_secure(variants, gates),
        _claim_comparisons(variants, gates),
        _claim_receiver_modes(variants, gates),
    ]
    variant_rows = [
        {
            "variant": v.name,
            "successful_samples": v.successful_samples,
            "failed_samples": v.failed_samples,
            "post_ids": v.post_ids,
            "failure_classes": dict(v.failure_classes),
            "receiver": {
                "semantic_angle_successes": v.semantic_angle_successes,
                "audit_assisted_recoveries": v.audit_assisted_recoveries,
                "pure_selection_recoveries": v.pure_selection_recoveries,
            },
        }
        for v in variants
    ]
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "manifest_version": manifest.get("version"),
        "confidence": manifest.get("confidence", 0.95),
        "gates": gates,
        "variants": variant_rows,
        "claims": claims,
    }


def render_claims_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Claims Report",
        "",
        f"Generated: {report['created_at_utc']}",
        f"Confidence: {report.get('confidence', 0.95):.0%}",
        "",
        "## Gates",
        "",
        f"- Provenance: {'pass' if report['gates']['provenance']['accepted'] else 'fail'}",
        f"- Sample floor: {'pass' if report['gates']['samples']['passed'] else 'fail'}",
        f"- Same post IDs: {'pass' if report['gates']['same_posts']['passed'] else 'fail'}",
        "",
        "## Claims",
        "",
        "| Claim | Status | Evidence |",
        "|---|---:|---|",
    ]
    for claim in report["claims"]:
        metric = json.dumps(claim["metric"], sort_keys=True)
        if len(metric) > 220:
            metric = metric[:217] + "..."
        lines.append(f"| {claim['id']} | {claim['status']} | `{metric}` |")
    lines.extend(["", "## Variants", "", "| Variant | Success | Failed | Pure | Audit-assisted |", "|---|---:|---:|---:|---:|"])
    for variant in report["variants"]:
        receiver = variant["receiver"]
        lines.append(
            f"| {variant['variant']} | {variant['successful_samples']} | {variant['failed_samples']} | "
            f"{receiver['pure_selection_recoveries']} | {receiver['audit_assisted_recoveries']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_claims_report(
    artifacts: list[dict[str, Any]],
    *,
    output_md: Path = DEFAULT_REPORT_MD,
    output_json: Path = DEFAULT_REPORT_JSON,
    accept_historical: bool = False,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    report = evaluate_claims(
        artifacts,
        accept_historical=accept_historical,
        manifest_path=manifest_path,
    )
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_claims_markdown(report), encoding="utf-8")
    output_json.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report
