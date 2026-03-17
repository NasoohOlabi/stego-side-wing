import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


class EvalStats(NamedTuple):
    comparisons: int
    unique_posts: int
    avg_kl: float
    avg_jsd: float
    per_post_avg_kl: dict[str, float]
    per_post_avg_jsd: dict[str, float]


def _progress_update_every(total: int) -> int:
    # Keep terminal updates responsive without printing thousands of times.
    return max(1, total // 120)


def render_progress(label: str, current: int, total: int, update_every: int) -> None:
    if total <= 0:
        return
    if current != total and (current % update_every != 0):
        return
    width = 28
    filled = int((current / total) * width)
    bar = "#" * filled + "-" * (width - filled)
    print(
        f"\r{label}: [{bar}] {current}/{total} ({(current / total) * 100:5.1f}%)",
        end="",
        flush=True,
    )
    if current == total:
        print()


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def extract_stego_text(file_path: Path) -> str | None:
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        return None
    first_item = data[0]
    if not isinstance(first_item, dict):
        return None
    stego = first_item.get("stegoText")
    return stego if isinstance(stego, str) else None


def extract_comment_counter(post_path: Path) -> Counter:
    data = json.loads(post_path.read_text(encoding="utf-8"))
    comments = data.get("comments", [])
    counter: Counter = Counter()
    for comment in comments:
        body = comment.get("body")
        if body:
            counter.update(tokenize(body))
    return counter


def load_primary_counters(dataset_dir: Path, post_ids: set[str]) -> dict[str, Counter]:
    counters: dict[str, Counter] = {}
    sorted_post_ids = sorted(post_ids)
    total = len(sorted_post_ids)
    update_every = _progress_update_every(total)
    for idx, post_id in enumerate(sorted_post_ids, start=1):
        post_path = dataset_dir / f"{post_id}.json"
        if post_path.exists():
            counters[post_id] = extract_comment_counter(post_path)
        render_progress("Loading primary baselines", idx, total, update_every)
    return counters


def load_global_stats(dataset_dir: Path) -> tuple[int, Counter, int]:
    post_paths = sorted(dataset_dir.glob("*.json"))
    total_posts = len(post_paths)
    update_every = _progress_update_every(total_posts)
    global_counter: Counter = Counter()
    nonempty_bodies = 0
    for idx, post_path in enumerate(post_paths, start=1):
        data = json.loads(post_path.read_text(encoding="utf-8"))
        comments = data.get("comments", [])
        post_counter: Counter = Counter()
        for comment in comments:
            body = comment.get("body")
            if body:
                post_counter.update(tokenize(body))
                nonempty_bodies += 1
        global_counter.update(post_counter)
        render_progress("Loading global baseline", idx, total_posts, update_every)
    return total_posts, global_counter, nonempty_bodies


def _smoothed_prob(counter: Counter, token: str, total: int, vocab_size: int, alpha: float) -> float:
    denom = total + alpha * vocab_size
    return (counter.get(token, 0) + alpha) / denom


def kl_divergence(p_counter: Counter, q_counter: Counter, alpha: float) -> float:
    vocab = set(p_counter) | set(q_counter)
    if not vocab:
        return 0.0

    p_total = sum(p_counter.values())
    q_total = sum(q_counter.values())
    if p_total == 0:
        return 0.0

    vocab_size = len(vocab)
    score = 0.0
    for token in vocab:
        p_prob = _smoothed_prob(p_counter, token, p_total, vocab_size, alpha)
        q_prob = _smoothed_prob(q_counter, token, q_total, vocab_size, alpha)
        score += p_prob * math.log(p_prob / q_prob)
    return score


def js_divergence(p_counter: Counter, q_counter: Counter, alpha: float) -> float:
    vocab = set(p_counter) | set(q_counter)
    if not vocab:
        return 0.0

    p_total = sum(p_counter.values())
    q_total = sum(q_counter.values())
    if p_total == 0:
        return 0.0

    vocab_size = len(vocab)
    m_probs: dict[str, float] = {}
    for token in vocab:
        p_prob = _smoothed_prob(p_counter, token, p_total, vocab_size, alpha)
        q_prob = _smoothed_prob(q_counter, token, q_total, vocab_size, alpha)
        m_probs[token] = 0.5 * (p_prob + q_prob)

    # m_counter stores probabilities, so we compute the two KL terms directly.
    kl_p_m = 0.0
    kl_q_m = 0.0
    for token in vocab:
        p_prob = _smoothed_prob(p_counter, token, p_total, vocab_size, alpha)
        q_prob = _smoothed_prob(q_counter, token, q_total, vocab_size, alpha)
        m_prob = m_probs[token]
        kl_p_m += p_prob * math.log(p_prob / m_prob)
        kl_q_m += q_prob * math.log(q_prob / m_prob)
    return 0.5 * (kl_p_m + kl_q_m)


def evaluate_baseline(
    stego_by_file: list[tuple[Path, str, Counter]],
    baseline_by_post: dict[str, Counter] | None,
    global_baseline: Counter | None,
    alpha: float,
) -> EvalStats:
    kl_scores: list[float] = []
    jsd_scores: list[float] = []
    per_post_kl: dict[str, list[float]] = defaultdict(list)
    per_post_jsd: dict[str, list[float]] = defaultdict(list)

    total = len(stego_by_file)
    update_every = _progress_update_every(total)
    for idx, (_, post_id, stego_counter) in enumerate(stego_by_file, start=1):
        if baseline_by_post is not None:
            baseline_counter = baseline_by_post.get(post_id)
            if baseline_counter is None or not baseline_counter:
                render_progress("Evaluating primary baseline", idx, total, update_every)
                continue
        else:
            if global_baseline is None or not global_baseline:
                render_progress("Evaluating secondary baseline", idx, total, update_every)
                continue
            baseline_counter = global_baseline

        kl_value = kl_divergence(stego_counter, baseline_counter, alpha=alpha)
        jsd_value = js_divergence(stego_counter, baseline_counter, alpha=alpha)
        kl_scores.append(kl_value)
        jsd_scores.append(jsd_value)
        per_post_kl[post_id].append(kl_value)
        per_post_jsd[post_id].append(jsd_value)
        if baseline_by_post is not None:
            render_progress("Evaluating primary baseline", idx, total, update_every)
        else:
            render_progress("Evaluating secondary baseline", idx, total, update_every)

    if not kl_scores:
        return EvalStats(0, 0, math.nan, math.nan, {}, {})

    per_post_avg_kl = {
        post_id: sum(scores) / len(scores) for post_id, scores in per_post_kl.items()
    }
    per_post_avg_jsd = {
        post_id: sum(scores) / len(scores) for post_id, scores in per_post_jsd.items()
    }
    return EvalStats(
        comparisons=len(kl_scores),
        unique_posts=len(per_post_avg_kl),
        avg_kl=sum(kl_scores) / len(kl_scores),
        avg_jsd=sum(jsd_scores) / len(jsd_scores),
        per_post_avg_kl=per_post_avg_kl,
        per_post_avg_jsd=per_post_avg_jsd,
    )


def _json_number(value: float) -> float | None:
    return value if math.isfinite(value) else None


def save_metrics_report(metrics_dir: Path, report: dict) -> Path:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = metrics_dir / f"divergence_metrics_{timestamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute KL and JSD of stego texts against primary and secondary baselines."
    )
    parser.add_argument(
        "--output-dir",
        default="output-results",
        help="Directory with generated output JSON files.",
    )
    parser.add_argument(
        "--dataset-dir",
        default="datasets/news_cleaned",
        help="Directory with original post JSON files.",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=1e-6,
        help="Additive smoothing constant.",
    )
    parser.add_argument(
        "--metrics-dir",
        default=r"D:\Master\code\stego-results-viewer\metrics",
        help="Directory where each run stores a JSON metrics report.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    dataset_dir = Path(args.dataset_dir)
    metrics_dir = Path(args.metrics_dir)
    output_files = sorted(output_dir.glob("*.json"))
    if not output_files:
        raise SystemExit(f"No output files found in: {output_dir}")

    file_to_post_id: dict[Path, str] = {}
    output_post_ids: set[str] = set()
    for file_path in output_files:
        post_id = file_path.stem.split("_version_")[0]
        file_to_post_id[file_path] = post_id
        output_post_ids.add(post_id)

    primary_counters = load_primary_counters(dataset_dir, output_post_ids)
    dataset_post_file_count, global_counter, global_nonempty_bodies = load_global_stats(dataset_dir)

    stego_by_file: list[tuple[Path, str, Counter]] = []
    skipped_no_stego = 0
    skipped_empty_stego = 0
    skipped_no_primary = 0
    skipped_empty_primary = 0

    stego_total = len(output_files)
    stego_update_every = _progress_update_every(stego_total)
    for idx, file_path in enumerate(output_files, start=1):
        post_id = file_to_post_id[file_path]
        stego_text = extract_stego_text(file_path)
        if stego_text is None:
            skipped_no_stego += 1
            render_progress("Loading stego samples", idx, stego_total, stego_update_every)
            continue

        stego_counter = Counter(tokenize(stego_text))
        if not stego_counter:
            skipped_empty_stego += 1
            render_progress("Loading stego samples", idx, stego_total, stego_update_every)
            continue

        stego_by_file.append((file_path, post_id, stego_counter))
        render_progress("Loading stego samples", idx, stego_total, stego_update_every)

    if not stego_by_file:
        raise SystemExit("No valid stego samples were found; cannot compute divergences.")

    primary_stats = evaluate_baseline(
        stego_by_file=stego_by_file,
        baseline_by_post=primary_counters,
        global_baseline=None,
        alpha=args.alpha,
    )
    secondary_stats = evaluate_baseline(
        stego_by_file=stego_by_file,
        baseline_by_post=None,
        global_baseline=global_counter,
        alpha=args.alpha,
    )
    for _, post_id, _ in stego_by_file:
        if post_id not in primary_counters:
            skipped_no_primary += 1
        elif not primary_counters[post_id]:
            skipped_empty_primary += 1

    print("=== Divergence Report ===")
    print(f"Tokenization: word unigram ({TOKEN_RE.pattern})")
    print(f"Smoothing alpha: {args.alpha}")
    print("KL direction: KL(stego || baseline)")
    print()
    print("=== Dataset Summary ===")
    print(f"Total output files: {len(output_files)}")
    print(f"Unique output post IDs: {len(output_post_ids)}")
    print(f"Usable stego samples: {len(stego_by_file)}")
    print(f"Skipped (missing stegoText): {skipped_no_stego}")
    print(f"Skipped (empty stegoText tokens): {skipped_empty_stego}")
    print(f"Skipped (missing primary post file): {skipped_no_primary}")
    print(f"Skipped (primary post has no non-empty comment bodies): {skipped_empty_primary}")
    print()
    print("=== Primary Baseline (matched-post comments) ===")
    print(f"Comparisons: {primary_stats.comparisons}")
    print(f"Posts represented: {primary_stats.unique_posts}")
    print(f"Average KL(stego || matched_post): {primary_stats.avg_kl:.12f}")
    print(f"Average JSD(stego, matched_post): {primary_stats.avg_jsd:.12f}")
    print()
    print("=== Secondary Baseline (global comments corpus) ===")
    print(f"Global source post files: {dataset_post_file_count}")
    print(f"Global non-empty comment bodies: {global_nonempty_bodies}")
    print(f"Comparisons: {secondary_stats.comparisons}")
    print(f"Stego posts represented: {secondary_stats.unique_posts}")
    print(f"Average KL(stego || global_corpus): {secondary_stats.avg_kl:.12f}")
    print(f"Average JSD(stego, global_corpus): {secondary_stats.avg_jsd:.12f}")

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "output_dir": str(output_dir.resolve()),
            "dataset_dir": str(dataset_dir.resolve()),
            "metrics_dir": str(metrics_dir.resolve()),
            "tokenization": "word_unigram",
            "token_regex": TOKEN_RE.pattern,
            "smoothing_alpha": args.alpha,
            "kl_direction": "KL(stego||baseline)",
        },
        "dataset_summary": {
            "total_output_files": len(output_files),
            "unique_output_post_ids": len(output_post_ids),
            "usable_stego_samples": len(stego_by_file),
            "skipped_missing_stegoText": skipped_no_stego,
            "skipped_empty_stego_tokens": skipped_empty_stego,
            "skipped_missing_primary_post_file": skipped_no_primary,
            "skipped_empty_primary_comment_bodies": skipped_empty_primary,
            "global_source_post_files": dataset_post_file_count,
            "global_nonempty_comment_bodies": global_nonempty_bodies,
        },
        "primary_baseline_matched_post": {
            "comparisons": primary_stats.comparisons,
            "stego_posts_represented": primary_stats.unique_posts,
            "average_kl_stego_vs_matched_post": _json_number(primary_stats.avg_kl),
            "average_jsd_stego_vs_matched_post": _json_number(primary_stats.avg_jsd),
        },
        "secondary_baseline_global_corpus": {
            "comparisons": secondary_stats.comparisons,
            "stego_posts_represented": secondary_stats.unique_posts,
            "average_kl_stego_vs_global_corpus": _json_number(secondary_stats.avg_kl),
            "average_jsd_stego_vs_global_corpus": _json_number(secondary_stats.avg_jsd),
        },
    }
    report_path = save_metrics_report(metrics_dir, report)
    print()
    print(f"Saved metrics JSON: {report_path}")


if __name__ == "__main__":
    main()
