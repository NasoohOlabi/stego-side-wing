from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from services.stego_metrics_service import run_single_post_metrics  # noqa: E402
from services.zlg_comparison_service import stegotext_has_prompt_leakage  # noqa: E402

TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
LEXICAL_QUALITY_INDEX_VERSION = "lexical_quality_v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_output_file(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.exists():
        return path
    marker = "metrics\\e2e_runs\\"
    idx = raw_path.lower().find(marker.lower())
    if idx >= 0:
        remapped = _REPO_ROOT / Path(raw_path[idx:].replace("\\", "/"))
        if remapped.exists():
            return remapped
    raise FileNotFoundError(raw_path)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(text)]


def _length_sanity_score(word_count: int) -> float:
    if word_count < 5:
        return word_count / 5.0
    if word_count <= 120:
        return 1.0
    return max(0.0, 1.0 - ((word_count - 120) / 120.0))


def _lexical_quality_index(
    *, unique_ratio: float, repetition_ratio: float, max_bigram_repeat: int, word_count: int
) -> float:
    if word_count == 0:
        return 0.0
    bigram_score = 1.0 - min(1.0, max(0, max_bigram_repeat - 1) / 3.0)
    score = (
        (0.4 * unique_ratio)
        + (0.3 * (1.0 - repetition_ratio))
        + (0.2 * bigram_score)
        + (0.1 * _length_sanity_score(word_count))
    )
    return round(100.0 * score, 6)


def _quality(text: str) -> dict[str, Any]:
    toks = _tokens(text)
    counts = Counter(toks)
    bigrams = Counter(zip(toks, toks[1:], strict=False))
    unique_ratio = (len(counts) / len(toks)) if toks else 0.0
    repetition_ratio = 1.0 - unique_ratio if toks else 0.0
    max_bigram_repeat = max(bigrams.values()) if bigrams else 0
    return {
        "word_count": len(toks),
        "unique_token_ratio": unique_ratio if toks else None,
        "repetition_ratio": repetition_ratio if toks else None,
        "single_token_share": (max(counts.values()) / len(toks)) if toks else None,
        "max_bigram_repeat": max_bigram_repeat,
        "lexical_quality_index": _lexical_quality_index(
            unique_ratio=unique_ratio,
            repetition_ratio=repetition_ratio,
            max_bigram_repeat=max_bigram_repeat,
            word_count=len(toks),
        ),
        "lexical_quality_index_version": LEXICAL_QUALITY_INDEX_VERSION,
        "char_count": len(text),
        "has_non_ascii": any(ord(ch) > 127 for ch in text),
    }


def _metric_block(metrics: dict[str, Any]) -> dict[str, Any]:
    primary = metrics.get("primary_baseline_matched_post") or {}
    secondary = metrics.get("secondary_baseline_global_corpus") or {}
    return {
        "perplexity_gpt2": metrics.get("perplexity"),
        "kl_matched_post": primary.get("kl_stego_vs_matched_post"),
        "jsd_matched_post": primary.get("jsd_stego_vs_matched_post"),
        "kl_global_corpus": secondary.get("kl_stego_vs_global_corpus"),
        "jsd_global_corpus": secondary.get("jsd_stego_vs_global_corpus"),
        "metric_warnings": metrics.get("warnings") or [],
    }


def _zlg_capacity_fields(row: dict[str, Any]) -> dict[str, int]:
    useful_bits = int(row.get("payload_bits_encoded") or 0)
    if useful_bits <= 0:
        useful_bits = max(0, int(row.get("payload_bytes_actual") or 0) * 8)
    total_bits = int(row.get("total_embedded_bits") or row.get("encoded_bits") or useful_bits)
    total_bits = max(total_bits, useful_bits)
    explicit_overhead = row.get("protocol_overhead_bits")
    overhead_bits = (
        max(0, int(explicit_overhead))
        if isinstance(explicit_overhead, (int, float))
        else total_bits - useful_bits
    )
    return {
        "payload_bits_encoded": useful_bits,
        "protocol_overhead_bits": overhead_bits,
        "total_embedded_bits": total_bits,
    }


def _write_temp_output(path: Path, stego_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([{"stegoText": stego_text}], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _finite_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    vals = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and math.isfinite(value):
            vals.append(float(value))
    return vals


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_method: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_method.setdefault(str(row["method"]), []).append(row)
    out: dict[str, Any] = {}
    for method, group in by_method.items():
        block: dict[str, Any] = {"n": len(group)}
        for key in (
            "payload_bits_encoded",
            "protocol_overhead_bits",
            "total_embedded_bits",
            "embedded_bits",
            "payload_bytes_encoded",
            "perplexity_gpt2",
            "kl_matched_post",
            "jsd_matched_post",
            "kl_global_corpus",
            "jsd_global_corpus",
            "word_count",
            "lexical_quality_index",
        ):
            vals = _finite_values(group, key)
            if vals:
                block[f"{key}_mean"] = statistics.fmean(vals)
                block[f"{key}_median"] = statistics.median(vals)
                block[f"{key}_min"] = min(vals)
                block[f"{key}_max"] = max(vals)
        out[method] = block
    return out


def _sign_test_p_value(positive: int, negative: int) -> float | None:
    total = positive + negative
    if total == 0:
        return None
    observed = min(positive, negative)
    tail = sum(math.comb(total, k) for k in range(observed + 1)) / (2**total)
    return min(1.0, 2.0 * tail)


def _bootstrap_mean_ci(
    values: list[float], *, iterations: int = 10_000, seed: int = 1337
) -> dict[str, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        statistics.fmean(values[rng.randrange(n)] for _ in range(n)) for _ in range(iterations)
    )
    return {
        "lower": means[int(0.025 * iterations)],
        "upper": means[min(iterations - 1, int(0.975 * iterations))],
    }


def _stats_from_complete_pairs(
    complete: list[dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    out: dict[str, Any] = {"paired_n": len(complete)}
    for metric in (
        "payload_bits_encoded",
        "total_embedded_bits",
        "embedded_bits",
        "payload_bytes_encoded",
        "perplexity_gpt2",
        "kl_matched_post",
        "jsd_matched_post",
        "kl_global_corpus",
        "jsd_global_corpus",
        "word_count",
        "repetition_ratio",
        "lexical_quality_index",
    ):
        deltas: list[float] = []
        for pair in complete:
            our_value = pair["our_method"].get(metric)
            zlg_value = pair["zlg"].get(metric)
            if not isinstance(our_value, (int, float)) or not isinstance(zlg_value, (int, float)):
                continue
            if not math.isfinite(float(our_value)) or not math.isfinite(float(zlg_value)):
                continue
            deltas.append(float(zlg_value) - float(our_value))
        if not deltas:
            continue
        positive = sum(delta > 0 for delta in deltas)
        negative = sum(delta < 0 for delta in deltas)
        block: dict[str, Any] = {
            "n": len(deltas),
            "mean_delta_zlg_minus_our": statistics.fmean(deltas),
            "median_delta_zlg_minus_our": statistics.median(deltas),
            "zlg_greater_count": positive,
            "our_greater_count": negative,
            "ties": len(deltas) - positive - negative,
            "two_sided_sign_test_p": _sign_test_p_value(positive, negative),
            "mean_delta_bootstrap_95_ci": _bootstrap_mean_ci(deltas),
        }
        if len(deltas) >= 2:
            block["delta_min"] = min(deltas)
            block["delta_max"] = max(deltas)
            block["delta_population_stdev"] = statistics.pstdev(deltas)
        out[metric] = block
    return out


def _row_level_paired_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[Any, dict[str, dict[str, Any]]] = {}
    for row in rows:
        pairs.setdefault(row.get("pair_id"), {})[str(row.get("method"))] = row
    complete = [pair for pair in pairs.values() if "our_method" in pair and "zlg" in pair]
    return _stats_from_complete_pairs(complete)


def _clustered_paired_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate repeated trials before inference so posts are the independent units."""
    clusters: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        key = str(row.get("post_id"))
        clusters.setdefault(key, {}).setdefault(str(row.get("method")), []).append(row)
    complete: list[dict[str, dict[str, Any]]] = []
    for methods in clusters.values():
        if "our_method" not in methods or "zlg" not in methods:
            continue
        aggregated: dict[str, dict[str, Any]] = {}
        for method in ("our_method", "zlg"):
            method_rows = methods[method]
            metric_row: dict[str, Any] = {}
            for metric in (
                "payload_bits_encoded",
                "total_embedded_bits",
                "embedded_bits",
                "payload_bytes_encoded",
                "perplexity_gpt2",
                "kl_matched_post",
                "jsd_matched_post",
                "kl_global_corpus",
                "jsd_global_corpus",
                "word_count",
                "repetition_ratio",
                "lexical_quality_index",
            ):
                values = _finite_values(method_rows, metric)
                if values:
                    metric_row[metric] = statistics.fmean(values)
            aggregated[method] = metric_row
        complete.append(aggregated)
    result = _stats_from_complete_pairs(complete)
    result["inference_unit"] = "unique_post_id"
    return result


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _diversity_diagnostics(
    rows: list[dict[str, Any]], *, minimum_ratio: float
) -> dict[str, Any]:
    texts_by_post: dict[str, list[str]] = {}
    for row in rows:
        if row.get("method") == "our_method":
            texts_by_post.setdefault(str(row.get("post_id")), []).append(
                _normalized_text(row.get("stegotext"))
            )
    posts = []
    for post_id, texts in sorted(texts_by_post.items()):
        ratio = len(set(texts)) / len(texts) if texts else 0.0
        posts.append({"post_id": post_id, "samples": len(texts), "unique_ratio": ratio})
    failing = [item for item in posts if item["unique_ratio"] < minimum_ratio]
    return {
        "minimum_unique_ratio": minimum_ratio,
        "posts": posts,
        "failing_post_ids": [item["post_id"] for item in failing],
        "passed": not failing,
    }


def _assert_diversity(rows: list[dict[str, Any]], *, minimum_ratio: float) -> dict[str, Any]:
    if not 0.0 <= minimum_ratio <= 1.0:
        raise ValueError("minimum diversity ratio must be between 0 and 1")
    diagnostics = _diversity_diagnostics(rows, minimum_ratio=minimum_ratio)
    if not diagnostics["passed"]:
        failed = ", ".join(diagnostics["failing_post_ids"])
        raise ValueError(f"our-method diversity guard failed for post(s): {failed}")
    return diagnostics


def _independence_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "row_pairs": len(rows) // 2,
        "independent_clusters": len({str(r.get("post_id")) for r in rows}),
        "unique_our_method_texts": len(
            {str(r.get("stegotext")) for r in rows if r.get("method") == "our_method"}
        ),
        "warning": (
            "Repeated trials from one post/sample are not independent. Primary p-values "
            "and confidence intervals are computed after cluster aggregation."
        ),
    }


def build_dataset(args: argparse.Namespace) -> dict[str, Any]:
    zlg_run_dir = Path(args.zlg_run_dir).resolve()
    zlg_rows = _load_jsonl(zlg_run_dir / "results.jsonl")
    source = _read_json(Path(args.source_summary).resolve())
    source_entries = [e for e in source.get("entries", []) if isinstance(e, dict)]
    source_by_key = {
        f"{e.get('post_id')}|{e.get('sample_index')}|{e.get('payload_hash')}|{e.get('output_file')}": e
        for e in source_entries
    }
    dataset_dir = Path(args.dataset_dir).resolve()
    temp_dir = zlg_run_dir / "comparison_dataset" / "metric_inputs"
    rows: list[dict[str, Any]] = []
    pair_id = 0

    for zlg in zlg_rows:
        zlg_text = str(zlg.get("stegotext") or "")
        if not zlg_text or not bool(zlg.get("accepted")):
            continue
        if stegotext_has_prompt_leakage(zlg_text):
            continue
        source_key = str(zlg.get("source_key") or "")
        source_entry = source_by_key.get(source_key)
        if source_entry is None:
            continue
        source_file = _resolve_output_file(str(source_entry["output_file"]))
        our_payload = _read_json(source_file)
        our_top = our_payload[0] if isinstance(our_payload, list) and our_payload else our_payload
        if not isinstance(our_top, dict):
            continue
        our_text = str(our_top.get("stegoText") or "")
        embedding = our_top.get("embedding") if isinstance(our_top.get("embedding"), dict) else {}
        comment_embedding = (
            embedding.get("commentEmbedding")
            if isinstance(embedding.get("commentEmbedding"), dict)
            else {}
        )
        compression = (
            embedding.get("compression") if isinstance(embedding.get("compression"), dict) else {}
        )
        recovery = source_entry.get("receiver_decode", {}).get("recovery_meta", {})
        capacity_metrics = (source_entry.get("sample_metrics") or {}).get("capacity_metrics") or {}
        # Pure-channel capacity: bits a blind receiver actually recovers from this one comment's
        # selection channel (+ any hidden linguistic-stego bits). Do NOT use
        # recovery.embedded_payload_bytes / source_entry.payload_bytes here -- those reflect
        # "audit_assisted_compressed_full" recovery (side-channel-assisted reconstruction of a
        # test-harness tracking string), not real transmitted capacity. See capacity_fields_note
        # in the written summary for the full explanation.
        pure_channel_bits = capacity_metrics.get("total_bits")
        if pure_channel_bits is None:
            pure_channel_bits = int(comment_embedding.get("bitsCount") or 0)
        else:
            pure_channel_bits = int(pure_channel_bits)
        our_metrics = run_single_post_metrics(source_file, dataset_dir, device=args.device)
        our_quality = _quality(our_text)
        rows.append(
            {
                "pair_id": pair_id,
                "post_id": source_entry.get("post_id"),
                "sample_index": source_entry.get("sample_index"),
                "slot": zlg.get("slot"),
                "method": "our_method",
                "comparison_mode": zlg.get("comparison_mode") or "legacy_unspecified",
                "stegotext": our_text,
                "embedded_bits": pure_channel_bits,
                "payload_bits_encoded": pure_channel_bits,
                "protocol_overhead_bits": 0,
                "total_embedded_bits": pure_channel_bits,
                "hidden_payload_bits": int(capacity_metrics.get("hidden_payload_bits") or 0),
                "selection_bits": int(capacity_metrics.get("selection_bits") or 0),
                "decode_ok": True,
                "quality_passed": True,
                "payload_bits_total": int(compression.get("compressedLength") or 0),
                "payload_bytes_target": int(source_entry.get("payload_bytes") or 0),
                "payload_bytes_encoded": pure_channel_bits / 8.0,
                "audit_assisted_payload_bytes": int(
                    recovery.get("embedded_payload_bytes") or source_entry.get("payload_bytes") or 0
                ),
                "recovery_source": "audit_assisted_compressed_full",
                "source_output_file": str(source_file),
                **our_quality,
                **_metric_block(our_metrics),
            }
        )

        zlg_metric_file = (
            temp_dir
            / f"{source_entry.get('post_id')}_version_zlg_{source_entry.get('sample_index')}_{pair_id}.json"
        )
        _write_temp_output(zlg_metric_file, zlg_text)
        zlg_metrics = run_single_post_metrics(zlg_metric_file, dataset_dir, device=args.device)
        zlg_quality = _quality(zlg_text)
        zlg_capacity = _zlg_capacity_fields(zlg)
        rows.append(
            {
                "pair_id": pair_id,
                "post_id": source_entry.get("post_id"),
                "sample_index": source_entry.get("sample_index"),
                "slot": zlg.get("slot"),
                "method": "zlg",
                "comparison_mode": zlg.get("comparison_mode") or "legacy_unspecified",
                "stegotext": zlg_text,
                "embedded_bits": zlg_capacity["total_embedded_bits"],
                **zlg_capacity,
                "decode_ok": bool(zlg.get("decode_ok")),
                "quality_passed": bool(zlg.get("quality_passed")),
                "payload_bits_total": int(zlg.get("target_bits") or 0),
                "payload_bytes_target": int(zlg.get("payload_bytes_target") or 0),
                "payload_bytes_encoded": int(zlg.get("payload_bytes_actual") or 0),
                "partial": bool(zlg.get("partial")),
                "api_ppl": zlg.get("ppl"),
                "recovery_source": "pure_channel_hide_reveal_verified",
                "source_output_file": str(source_file),
                **zlg_quality,
                **_metric_block(zlg_metrics),
            }
        )
        pair_id += 1

    diversity = _assert_diversity(rows, minimum_ratio=args.minimum_diversity_ratio)
    out_dir = zlg_run_dir / "comparison_dataset"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "paired_rows.jsonl"
    summary_path = out_dir / "summary.json"
    rows_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    summary = {
        "zlg_run_dir": str(zlg_run_dir),
        "rows": len(rows),
        "paired_posts": len({(r["post_id"], r["sample_index"]) for r in rows}) if rows else 0,
        "methods": _summary(rows),
        "comparison_modes": sorted({str(r.get("comparison_mode")) for r in rows}),
        "paired_statistics": _clustered_paired_stats(rows),
        "row_level_descriptive_statistics": _row_level_paired_stats(rows),
        "independence_diagnostics": _independence_diagnostics(rows),
        "diversity_guard": diversity,
        "lexical_quality_index": {
            "version": LEXICAL_QUALITY_INDEX_VERSION,
            "range": [0.0, 100.0],
            "higher_is_better": True,
            "weights": {
                "unique_token_ratio": 0.4,
                "inverse_repetition_ratio": 0.3,
                "bigram_non_repetition": 0.2,
                "length_sanity_5_to_120_words": 0.1,
            },
        },
        "rows_jsonl": str(rows_path),
        "capacity_fields_note": (
            "our_method capacity fields (embedded_bits/payload_bits_encoded/total_embedded_bits/"
            "payload_bytes_encoded) use the pure-selection-channel capacity "
            "(capacity_metrics.total_bits from the source e2e run), which is the honest "
            "blind-decode capacity. This is NOT the audit-assisted 'full payload' byte count "
            "(preserved separately under audit_assisted_payload_bytes) -- that number reflects "
            "recovery of a test-harness tracking string via side-channel-assisted reconstruction, "
            "not real pure-channel capacity."
        ),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build paired our-method vs ZLG comparison rows.")
    parser.add_argument("--zlg-run-dir", required=True)
    parser.add_argument(
        "--source-summary",
        default=str(
            _REPO_ROOT / "metrics/e2e_runs/fresh_metrics_200_20260509T233342Z/balanced/summary.json"
        ),
    )
    parser.add_argument(
        "--dataset-dir",
        default=str(
            _REPO_ROOT / "metrics/e2e_runs/fresh_metrics_200_20260509T233342Z/balanced/dataset"
        ),
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--minimum-diversity-ratio", type=float, default=1.0)
    parser.add_argument(
        "--refresh-statistics-only",
        action="store_true",
        help="Recompute clustered inference from existing paired rows without rescoring.",
    )
    args = parser.parse_args()
    if args.refresh_statistics_only:
        dataset_dir = Path(args.zlg_run_dir).resolve() / "comparison_dataset"
        rows = _load_jsonl(dataset_dir / "paired_rows.jsonl")
        diversity = _assert_diversity(rows, minimum_ratio=args.minimum_diversity_ratio)
        summary_path = dataset_dir / "summary.json"
        summary = _read_json(summary_path)
        summary["row_level_descriptive_statistics"] = _row_level_paired_stats(rows)
        summary["paired_statistics"] = _clustered_paired_stats(rows)
        summary["independence_diagnostics"] = _independence_diagnostics(rows)
        summary["diversity_guard"] = diversity
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(build_dataset(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
