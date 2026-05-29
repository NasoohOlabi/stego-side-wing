from __future__ import annotations

import argparse
import json
import math
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


def _quality(text: str) -> dict[str, Any]:
    toks = _tokens(text)
    counts = Counter(toks)
    bigrams = Counter(zip(toks, toks[1:]))
    return {
        "word_count": len(toks),
        "unique_token_ratio": (len(counts) / len(toks)) if toks else None,
        "repetition_ratio": 1.0 - (len(counts) / len(toks)) if toks else None,
        "single_token_share": (max(counts.values()) / len(toks)) if toks else None,
        "max_bigram_repeat": max(bigrams.values()) if bigrams else 0,
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


def _write_temp_output(path: Path, stego_text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"stegoText": stego_text}], ensure_ascii=False, indent=2), encoding="utf-8")


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


def _paired_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[tuple[Any, Any], dict[str, dict[str, Any]]] = {}
    for row in rows:
        pairs.setdefault((row.get("post_id"), row.get("sample_index")), {})[str(row.get("method"))] = row
    complete = [pair for pair in pairs.values() if "our_method" in pair and "zlg" in pair]
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
        }
        if len(deltas) >= 2:
            block["delta_min"] = min(deltas)
            block["delta_max"] = max(deltas)
            block["delta_population_stdev"] = statistics.pstdev(deltas)
        out[metric] = block
    return out


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
        comment_embedding = embedding.get("commentEmbedding") if isinstance(embedding.get("commentEmbedding"), dict) else {}
        compression = embedding.get("compression") if isinstance(embedding.get("compression"), dict) else {}
        recovery = source_entry.get("receiver_decode", {}).get("recovery_meta", {})
        our_metrics = run_single_post_metrics(source_file, dataset_dir, device=args.device)
        our_quality = _quality(our_text)
        rows.append(
            {
                "post_id": source_entry.get("post_id"),
                "sample_index": source_entry.get("sample_index"),
                "method": "our_method",
                "stegotext": our_text,
                "embedded_bits": int(comment_embedding.get("bitsCount") or 0),
                "payload_bits_encoded": int(comment_embedding.get("bitsCount") or 0),
                "protocol_overhead_bits": 0,
                "total_embedded_bits": int(comment_embedding.get("bitsCount") or 0),
                "decode_ok": True,
                "quality_passed": True,
                "payload_bits_total": int(compression.get("compressedLength") or 0),
                "payload_bytes_target": int(source_entry.get("payload_bytes") or 0),
                "payload_bytes_encoded": int(recovery.get("embedded_payload_bytes") or source_entry.get("payload_bytes") or 0),
                "source_output_file": str(source_file),
                **our_quality,
                **_metric_block(our_metrics),
            }
        )

        zlg_metric_file = temp_dir / f"{source_entry.get('post_id')}_zlg_{source_entry.get('sample_index')}.json"
        _write_temp_output(zlg_metric_file, zlg_text)
        zlg_metrics = run_single_post_metrics(zlg_metric_file, dataset_dir, device=args.device)
        zlg_quality = _quality(zlg_text)
        rows.append(
            {
                "post_id": source_entry.get("post_id"),
                "sample_index": source_entry.get("sample_index"),
                "method": "zlg",
                "stegotext": zlg_text,
                "embedded_bits": int(zlg.get("payload_bits_encoded") or zlg.get("encoded_bits") or 0),
                "payload_bits_encoded": int(zlg.get("payload_bits_encoded") or 0),
                "protocol_overhead_bits": int(zlg.get("protocol_overhead_bits") or 0),
                "total_embedded_bits": int(zlg.get("total_embedded_bits") or zlg.get("encoded_bits") or 0),
                "decode_ok": bool(zlg.get("decode_ok")),
                "quality_passed": bool(zlg.get("quality_passed")),
                "payload_bits_total": int(zlg.get("target_bits") or 0),
                "payload_bytes_target": int(zlg.get("payload_bytes_target") or 0),
                "payload_bytes_encoded": int(zlg.get("payload_bytes_actual") or 0),
                "partial": bool(zlg.get("partial")),
                "api_ppl": zlg.get("ppl"),
                "source_output_file": str(source_file),
                **zlg_quality,
                **_metric_block(zlg_metrics),
            }
        )

    out_dir = zlg_run_dir / "comparison_dataset"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "paired_rows.jsonl"
    summary_path = out_dir / "summary.json"
    rows_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    summary = {
        "zlg_run_dir": str(zlg_run_dir),
        "rows": len(rows),
        "paired_posts": len({(r["post_id"], r["sample_index"]) for r in rows}) if rows else 0,
        "methods": _summary(rows),
        "paired_statistics": _paired_stats(rows),
        "rows_jsonl": str(rows_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build paired our-method vs ZLG comparison rows.")
    parser.add_argument("--zlg-run-dir", required=True)
    parser.add_argument(
        "--source-summary",
        default=str(_REPO_ROOT / "metrics/e2e_runs/fresh_metrics_200_20260509T233342Z/balanced/summary.json"),
    )
    parser.add_argument(
        "--dataset-dir",
        default=str(_REPO_ROOT / "metrics/e2e_runs/fresh_metrics_200_20260509T233342Z/balanced/dataset"),
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()
    print(json.dumps(build_dataset(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
