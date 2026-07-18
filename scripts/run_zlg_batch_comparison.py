from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from services.zlg_comparison_service import ComparisonInput, append_jsonl, run_comparison_sample

DEFAULT_SOURCE_SUMMARY = (
    _REPO_ROOT
    / "metrics"
    / "e2e_runs"
    / "fresh_metrics_200_20260509T233342Z"
    / "balanced"
    / "summary.json"
)


def _read_json_obj(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON in {path}")
    return payload


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars]


def _split_sentences(text: str) -> list[str]:
    text = text.replace("\r", "\n")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [part.strip() for part in parts if part.strip()]


def _extract_selftext_sentences(raw_selftext: str, max_selftext_chars: int) -> list[str]:
    clipped = _clip(raw_selftext, max_selftext_chars)
    if not clipped:
        return []
    try:
        parsed = json.loads(clipped)
    except Exception:
        return _split_sentences(clipped)
    if not isinstance(parsed, dict):
        return _split_sentences(clipped)
    parts: list[str] = []
    title = str(parsed.get("title") or "").strip()
    summary = str(parsed.get("summary") or "").strip()
    author = str(parsed.get("author") or "").strip()
    key_points = parsed.get("key_points")
    if title:
        parts.append(title)
    if summary:
        parts.append(summary)
    if isinstance(key_points, list):
        parts.extend(str(item).strip() for item in key_points if str(item).strip())
    if author and author.lower() != "unknown":
        parts.append(author)
    return [part for sentence in parts for part in _split_sentences(sentence)]


def _build_cover_texts(
    context: dict[str, Any],
    picked: list[dict[str, Any]],
    max_selftext_chars: int,
    max_chain_chars: int,
) -> list[str]:
    title = str(context.get("title") or "").strip()
    selftext = str(context.get("selftext") or "")
    comment_lines = [
        _clip(str(item.get("body") or ""), max_chain_chars)
        for item in picked
        if str(item.get("body") or "").strip()
    ]
    comment_candidates: list[str] = []
    for line in comment_lines:
        comment_candidates.extend(_split_sentences(line))
    comment_candidates = [
        sentence for sentence in comment_candidates if 4 <= len(_tokens_for_prompt(sentence)) <= 60
    ]
    if comment_candidates:
        return _dedupe_sentences(comment_candidates)[:32]

    candidates: list[str] = []
    if title:
        candidates.extend(_split_sentences(title))
    candidates.extend(_extract_selftext_sentences(selftext, max_selftext_chars))
    for line in comment_lines:
        candidates.extend(_split_sentences(line))
    return _dedupe_sentences(candidates)[:32]


def _tokens_for_prompt(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9']+", text)


def _dedupe_sentences(candidates: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for sentence in candidates:
        normalized = sentence.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _seed_from_key(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _entry_key(entry: dict[str, Any]) -> str:
    return (
        f"{entry.get('post_id')}|{entry.get('sample_index')}|"
        f"{entry.get('payload_hash')}|{entry.get('output_file')}"
    )


def _load_processed(results_jsonl: Path) -> set[str]:
    done: set[str] = set()
    if not results_jsonl.exists():
        return done
    with results_jsonl.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("source_key"), str):
                done.add(row["source_key"])
    return done


def _load_existing_counts(results_jsonl: Path) -> tuple[int, int, int]:
    processed = 0
    accepted = 0
    failed = 0
    if not results_jsonl.exists():
        return processed, accepted, failed
    with results_jsonl.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            processed += 1
            if bool(row.get("accepted")):
                accepted += 1
            else:
                failed += 1
    return processed, accepted, failed


def _utf8_prefix_by_bytes(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    out: list[str] = []
    used = 0
    for char in text:
        char_bytes = len(char.encode("utf-8"))
        if used + char_bytes > max_bytes:
            break
        out.append(char)
        used += char_bytes
    return "".join(out)


def _payload_candidates_for_mode(
    mode: str, our_embedded_bits: int, configured: tuple[int, ...]
) -> tuple[int, ...]:
    if configured:
        return configured
    if mode == "capacity_matched":
        return (max(1, int(our_embedded_bits)),)
    if mode == "max_capacity":
        return ComparisonInput.payload_bits_candidates
    raise ValueError(f"Unknown comparison mode: {mode}")


def _extract_sample(
    entry: dict[str, Any], max_selftext_chars: int, max_chain_chars: int
) -> tuple[list[str], str, int]:
    raw_output_file = str(entry["output_file"])
    output_file = Path(raw_output_file)
    if not output_file.exists():
        marker = "metrics\\e2e_runs\\"
        lower = raw_output_file.lower()
        idx = lower.find(marker.lower())
        if idx >= 0:
            rel = raw_output_file[idx:].replace("\\", "/")
            remapped = _REPO_ROOT / Path(rel)
            if remapped.exists():
                output_file = remapped
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError(f"Unexpected output payload shape: {output_file}")
    top = payload[0]
    embedding = top.get("embedding")
    if not isinstance(embedding, dict):
        raise ValueError("missing embedding")
    compression = embedding.get("compression")
    comment_embedding = embedding.get("commentEmbedding")
    if not isinstance(compression, dict) or not isinstance(comment_embedding, dict):
        raise ValueError("missing compression/commentEmbedding")
    target_payload = str(compression.get("payload") or "")
    embedded_bits = int(comment_embedding.get("bitsCount") or 0)
    context = comment_embedding.get("context")
    picked = comment_embedding.get("pickedCommentChain")
    if not isinstance(context, dict) or not isinstance(picked, list):
        raise ValueError("missing context/pickedCommentChain")
    picked_rows = [x for x in picked if isinstance(x, dict)]
    cover_texts = _build_cover_texts(
        context,
        picked_rows,
        max_selftext_chars=max_selftext_chars,
        max_chain_chars=max_chain_chars,
    )
    if len(cover_texts) < 2:
        raise ValueError("not enough cover sentences extracted for ZLG prompt")
    return cover_texts, target_payload, embedded_bits


def main() -> int:
    parser = argparse.ArgumentParser(description="Long-running ZLG batch comparison runner")
    parser.add_argument("--source-summary", default=str(DEFAULT_SOURCE_SUMMARY))
    parser.add_argument("--server-url", default="http://127.0.0.1:9000")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--request-timeout-seconds", type=int, default=3600)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--no-reveal-check", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-selftext-chars", type=int, default=2500)
    parser.add_argument("--max-chain-chars", type=int, default=1400)
    parser.add_argument(
        "--comparison-mode",
        choices=("capacity_matched", "max_capacity"),
        default="capacity_matched",
        help="Match useful payload bits, or independently probe ZLG's maximum capacity.",
    )
    parser.add_argument(
        "--match-our-embedded-bits",
        action="store_true",
        help="Deprecated alias for --comparison-mode capacity_matched.",
    )
    parser.add_argument("--zlg-max-new-tokens", type=int, default=48)
    parser.add_argument("--zlg-quality-max-words", type=int, default=40)
    parser.add_argument(
        "--zlg-payload-bit-candidates",
        default="",
        help="Comma-separated raw payload bit candidates for /capacity_probe.",
    )
    args = parser.parse_args()

    source_summary = Path(args.source_summary).resolve()
    source = _read_json_obj(source_summary)
    entries_raw = source.get("entries", [])
    entries = [e for e in entries_raw if isinstance(e, dict) and e.get("output_file")]
    if args.limit > 0:
        entries = entries[: args.limit]

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        Path(args.run_dir).resolve()
        if args.run_dir
        else (_REPO_ROOT / "metrics" / "zlg_comparison_runs" / f"zlg_batch_{run_id}").resolve()
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    results_jsonl = run_dir / "results.jsonl"
    progress_path = run_dir / "progress.json"
    summary_path = run_dir / "summary.json"
    if args.overwrite and results_jsonl.exists():
        results_jsonl.unlink()

    done = _load_processed(results_jsonl)
    processed, accepted, failed = _load_existing_counts(results_jsonl)
    payload_bit_candidates = tuple(
        int(part.strip())
        for part in str(args.zlg_payload_bit_candidates).split(",")
        if part.strip()
    )
    comparison_mode = "capacity_matched" if args.match_our_embedded_bits else args.comparison_mode

    for idx, entry in enumerate(entries):
        key = _entry_key(entry)
        if key in done:
            continue
        try:
            cover_texts, full_target_payload, our_embedded_bits = _extract_sample(
                entry,
                max(0, args.max_selftext_chars),
                max(0, args.max_chain_chars),
            )
            target_payload = full_target_payload
            if comparison_mode == "capacity_matched":
                fair_payload_bytes = max(1, (our_embedded_bits + 7) // 8)
                target_payload = _utf8_prefix_by_bytes(full_target_payload, fair_payload_bytes)
                if not target_payload:
                    target_payload = "A"
            result = run_comparison_sample(
                ComparisonInput(
                    target_payload=target_payload,
                    server_url=args.server_url,
                    cover_texts=cover_texts,
                    seed=_seed_from_key(key),
                    max_retries=max(1, args.max_retries),
                    do_reveal_check=not args.no_reveal_check,
                    request_timeout_seconds=max(1, args.request_timeout_seconds),
                    n_cover=4,
                    max_new_tokens=max(1, args.zlg_max_new_tokens),
                    quality_max_words=max(1, args.zlg_quality_max_words),
                    payload_bits_candidates=_payload_candidates_for_mode(
                        comparison_mode, our_embedded_bits, payload_bit_candidates
                    ),
                    use_capacity_probe=comparison_mode == "max_capacity",
                )
            )
            row = {
                "source_key": key,
                "post_id": entry.get("post_id"),
                "sample_index": entry.get("sample_index"),
                "payload_hash": entry.get("payload_hash"),
                "source_output_file": entry.get("output_file"),
                "cover_texts": cover_texts,
                "payload": target_payload,
                "full_payload": full_target_payload,
                "our_embedded_bits_budget": our_embedded_bits,
                "matched_payload_bytes_target": len(target_payload.encode("utf-8")),
                "comparison_mode": comparison_mode,
                "payload_bits_candidates": list(
                    _payload_candidates_for_mode(
                        comparison_mode, our_embedded_bits, payload_bit_candidates
                    )
                ),
                **result,
            }
        except Exception as exc:
            row = {
                "source_key": key,
                "post_id": entry.get("post_id"),
                "sample_index": entry.get("sample_index"),
                "payload_hash": entry.get("payload_hash"),
                "source_output_file": entry.get("output_file"),
                "accepted": False,
                "reason": f"sample_extract_failed: {exc}",
                "payload_bytes_target": 0,
                "payload_bytes_actual": 0,
                "stegotext": None,
                "decode_ok": None,
                "ppl": None,
                "params_used": None,
                "latency_ms": 0,
                "attempt": 0,
            }
        append_jsonl(results_jsonl, row)
        done.add(key)
        processed += 1
        if bool(row.get("accepted")):
            accepted += 1
        else:
            failed += 1
        progress = {
            "source_summary": str(source_summary),
            "run_dir": str(run_dir),
            "total_entries": len(entries),
            "processed_now": processed,
            "accepted_now": accepted,
            "failed_now": failed,
            "last_index": idx,
            "updated_at_utc": datetime.now(UTC).isoformat(),
        }
        progress_path.write_text(
            json.dumps(progress, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    summary = {
        "source_summary": str(source_summary),
        "run_dir": str(run_dir),
        "total_entries": len(entries),
        "processed_entries": len(done.intersection({_entry_key(e) for e in entries})),
        "accepted": accepted,
        "failed": failed,
        "comparison_mode": comparison_mode,
        "results_jsonl": str(results_jsonl),
        "updated_at_utc": datetime.now(UTC).isoformat(),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
