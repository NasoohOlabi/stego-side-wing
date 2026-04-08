"""Stego output metrics: GPT-style perplexity and word-unigram KL/JSD vs baselines."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple, cast

from loguru import logger

from infrastructure.config import REPO_ROOT

TOKEN_RE = re.compile(r"[A-Za-z0-9']+")

_METRICS_CLI_LOG = logger.bind(component="StegoMetricsCLI")


class EvalStats(NamedTuple):
    """Aggregated KL/JSD scores for one baseline type."""

    comparisons: int
    unique_posts: int
    avg_kl: float
    avg_jsd: float
    per_post_avg_kl: dict[str, float]
    per_post_avg_jsd: dict[str, float]


def _json_number(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _maybe_progress(
    hook: Callable[[str, int, int], None] | None,
    label: str,
    current: int,
    total: int,
) -> None:
    if hook is None or total <= 0:
        return
    every = max(1, total // 120)
    if current != total and (current % every != 0):
        return
    hook(label, current, total)


def metrics_cli_progress(label: str, current: int, total: int) -> None:
    """Structured progress for CLI scripts (no raw stdout); use DEBUG for each tick."""
    if total <= 0:
        return
    width = 28
    filled = int((current / total) * width)
    pct = (current / total) * 100
    _METRICS_CLI_LOG.debug(
        "metrics_cli_progress",
        label=label,
        current=current,
        total=total,
        pct_rounded=round(pct, 1),
        bar_filled=filled,
        bar_width=width,
    )
    if current == total:
        _METRICS_CLI_LOG.info(
            "metrics_cli_progress_complete",
            label=label,
            total=total,
        )


def save_perplexity_report(metrics_dir: Path, report: dict[str, Any]) -> Path:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = metrics_dir / f"perplexity_metrics_{timestamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report_path


def save_divergence_report(metrics_dir: Path, report: dict[str, Any]) -> Path:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = metrics_dir / f"divergence_metrics_{timestamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report_path


def extract_stego_text_perplexity(data: Any) -> str | None:
    if isinstance(data, dict):
        stego = data.get("stego_text")
        if isinstance(stego, str):
            return stego
        stego = data.get("stegoText")
        if isinstance(stego, str):
            return stego
        return None
    if isinstance(data, list) and data:
        first_item = data[0]
        if isinstance(first_item, dict):
            stego = first_item.get("stegoText")
            if isinstance(stego, str):
                return stego
    return None


def extract_stego_text_unified(data: Any) -> str | None:
    """Stego string for single-post metrics: array stegoText first, then dict keys."""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            stego = first.get("stegoText")
            if isinstance(stego, str):
                return stego
    if isinstance(data, dict):
        stego = data.get("stegoText")
        if isinstance(stego, str):
            return stego
        stego = data.get("stego_text")
        if isinstance(stego, str):
            return stego
    return None


def resolve_device(torch_module: Any, device_arg: str) -> str:
    if device_arg != "auto":
        return device_arg
    return "cuda" if torch_module.cuda.is_available() else "cpu"


def _ppl_chunk_loss(
    causal_lm: Any, chunk_ids: Any, target_len: int, torch_module: Any
) -> tuple[float, int]:
    target_ids = chunk_ids.clone()
    target_ids[:, :-target_len] = -100
    with torch_module.no_grad():
        outputs = causal_lm(chunk_ids, labels=target_ids)
        loss = float(outputs.loss.item())
    return loss * target_len, target_len


def compute_text_perplexity(
    tokenizer: Any,
    causal_lm: Any,
    torch_module: Any,
    text: str,
    stride: int,
    max_length: int,
    device: str,
) -> float:
    encoded = tokenizer(text, return_tensors="pt", truncation=False)
    input_ids = encoded["input_ids"].to(device)
    seq_len = input_ids.size(1)
    if seq_len < 2:
        return math.nan
    total_nll = 0.0
    total_tokens = 0
    for i in range(0, seq_len, stride):
        begin_loc = max(i + stride - max_length, 0)
        end_loc = min(i + stride, seq_len)
        target_len = end_loc - begin_loc
        if target_len <= 0:
            continue
        chunk_ids = input_ids[:, begin_loc:end_loc]
        inc_nll, inc_tok = _ppl_chunk_loss(causal_lm, chunk_ids, target_len, torch_module)
        total_nll += inc_nll
        total_tokens += inc_tok
        if end_loc >= seq_len:
            break
    if total_tokens <= 0:
        return math.nan
    return float(math.exp(total_nll / total_tokens))


def _scan_perplexity_files(
    output_dir: Path,
    hook: Callable[[str, int, int], None] | None,
) -> tuple[list[tuple[Path, str]], dict[str, int]]:
    output_files = sorted(output_dir.glob("*.json"))
    counts = {
        "skipped_invalid_json": 0,
        "skipped_missing_stego": 0,
        "skipped_empty_text": 0,
    }
    valid: list[tuple[Path, str]] = []
    total = len(output_files)
    for idx, file_path in enumerate(output_files, start=1):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            counts["skipped_invalid_json"] += 1
            _maybe_progress(hook, "Reading output files", idx, total)
            continue
        stego_text = extract_stego_text_perplexity(data)
        if stego_text is None:
            counts["skipped_missing_stego"] += 1
            _maybe_progress(hook, "Reading output files", idx, total)
            continue
        if not stego_text.strip():
            counts["skipped_empty_text"] += 1
            _maybe_progress(hook, "Reading output files", idx, total)
            continue
        valid.append((file_path, stego_text))
        _maybe_progress(hook, "Reading output files", idx, total)
    return valid, counts


def _score_perplexity_batch(
    valid_entries: list[tuple[Path, str]],
    tokenizer: Any,
    causal_lm: Any,
    torch_module: Any,
    stride: int,
    max_length: int,
    device: str,
    hook: Callable[[str, int, int], None] | None,
) -> tuple[list[float], list[dict[str, Any]]]:
    perplexities: list[float] = []
    file_scores: list[dict[str, Any]] = []
    total = len(valid_entries)
    for idx, (file_path, stego_text) in enumerate(valid_entries, start=1):
        ppl = compute_text_perplexity(
            tokenizer=tokenizer,
            causal_lm=causal_lm,
            torch_module=torch_module,
            text=stego_text,
            stride=stride,
            max_length=max_length,
            device=device,
        )
        if math.isfinite(ppl):
            perplexities.append(ppl)
            file_scores.append({"file": str(file_path), "perplexity": ppl})
        _maybe_progress(hook, "Computing perplexity", idx, total)
    return perplexities, file_scores


def run_perplexity_metrics(
    output_dir: Path,
    metrics_dir: Path,
    *,
    model_name: str = "gpt2",
    stride: int = 512,
    device: str = "auto",
    progress_hook: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Load stego texts from output JSON, score with a causal LM, save report under metrics_dir."""
    if stride <= 0:
        raise ValueError("stride must be a positive integer")
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output_dir not found or not a directory: {output_dir}")
    valid_entries, skip_counts = _scan_perplexity_files(output_dir, progress_hook)
    if not valid_entries:
        raise ValueError("No valid stego texts were found; cannot compute perplexity.")
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise ImportError(
            "Missing dependency for perplexity evaluation. Install transformers and torch."
        ) from exc
    resolved_device = resolve_device(torch, device)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    causal_lm = cast(Any, AutoModelForCausalLM.from_pretrained(model_name))
    causal_lm.to(resolved_device)
    causal_lm.eval()
    max_length = int(getattr(causal_lm.config, "n_positions", 1024))
    perplexities, file_scores = _score_perplexity_batch(
        valid_entries,
        tokenizer,
        causal_lm,
        torch,
        stride,
        max_length,
        resolved_device,
        progress_hook,
    )
    if not perplexities:
        raise ValueError("Perplexity computation produced no valid scores.")
    output_files = sorted(output_dir.glob("*.json"))
    avg_ppl = sum(perplexities) / len(perplexities)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "output_dir": str(output_dir.resolve()),
            "metrics_dir": str(metrics_dir.resolve()),
            "model_name": model_name,
            "device": resolved_device,
            "stride": stride,
            "max_length": max_length,
        },
        "dataset_summary": {
            "total_output_files": len(output_files),
            "usable_stego_texts": len(valid_entries),
            "skipped_invalid_json": skip_counts["skipped_invalid_json"],
            "skipped_missing_stego_text": skip_counts["skipped_missing_stego"],
            "skipped_empty_stego_text": skip_counts["skipped_empty_text"],
            "scored_texts": len(perplexities),
        },
        "perplexity_summary": {
            "average_perplexity": _json_number(avg_ppl),
            "min_perplexity": _json_number(min(perplexities)),
            "max_perplexity": _json_number(max(perplexities)),
        },
        "per_file_perplexity": file_scores,
    }
    report_path = save_perplexity_report(metrics_dir, report)
    return {"report": report, "report_path": str(report_path.resolve())}


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def extract_stego_text_divergence(file_path: Path) -> str | None:
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


def load_primary_counters(
    dataset_dir: Path,
    post_ids: set[str],
    hook: Callable[[str, int, int], None] | None,
) -> dict[str, Counter]:
    counters: dict[str, Counter] = {}
    sorted_post_ids = sorted(post_ids)
    total = len(sorted_post_ids)
    for idx, post_id in enumerate(sorted_post_ids, start=1):
        post_path = dataset_dir / f"{post_id}.json"
        if post_path.exists():
            counters[post_id] = extract_comment_counter(post_path)
        _maybe_progress(hook, "Loading primary baselines", idx, total)
    return counters


def load_global_stats(
    dataset_dir: Path,
    hook: Callable[[str, int, int], None] | None,
) -> tuple[int, Counter, int]:
    post_paths = sorted(dataset_dir.glob("*.json"))
    total_posts = len(post_paths)
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
        _maybe_progress(hook, "Loading global baseline", idx, total_posts)
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
    hook: Callable[[str, int, int], None] | None,
    primary: bool,
) -> EvalStats:
    kl_scores: list[float] = []
    jsd_scores: list[float] = []
    per_post_kl: dict[str, list[float]] = defaultdict(list)
    per_post_jsd: dict[str, list[float]] = defaultdict(list)
    total = len(stego_by_file)
    label = "Evaluating primary baseline" if primary else "Evaluating secondary baseline"
    for idx, (_, post_id, stego_counter) in enumerate(stego_by_file, start=1):
        if baseline_by_post is not None:
            baseline_counter = baseline_by_post.get(post_id)
            if baseline_counter is None or not baseline_counter:
                _maybe_progress(hook, label, idx, total)
                continue
        else:
            if global_baseline is None or not global_baseline:
                _maybe_progress(hook, label, idx, total)
                continue
            baseline_counter = global_baseline
        kl_scores.append(kl_divergence(stego_counter, baseline_counter, alpha=alpha))
        jsd_scores.append(js_divergence(stego_counter, baseline_counter, alpha=alpha))
        per_post_kl[post_id].append(kl_scores[-1])
        per_post_jsd[post_id].append(jsd_scores[-1])
        _maybe_progress(hook, label, idx, total)
    if not kl_scores:
        return EvalStats(0, 0, math.nan, math.nan, {}, {})
    per_post_avg_kl = {pid: sum(s) / len(s) for pid, s in per_post_kl.items()}
    per_post_avg_jsd = {pid: sum(s) / len(s) for pid, s in per_post_jsd.items()}
    return EvalStats(
        comparisons=len(kl_scores),
        unique_posts=len(per_post_avg_kl),
        avg_kl=sum(kl_scores) / len(kl_scores),
        avg_jsd=sum(jsd_scores) / len(jsd_scores),
        per_post_avg_kl=per_post_avg_kl,
        per_post_avg_jsd=per_post_avg_jsd,
    )


def _collect_divergence_samples(
    output_dir: Path,
    hook: Callable[[str, int, int], None] | None,
) -> tuple[list[tuple[Path, str, Counter]], set[str], dict[str, int], int]:
    output_files = sorted(output_dir.glob("*.json"))
    file_to_post_id = {fp: fp.stem.split("_version_")[0] for fp in output_files}
    output_post_ids = set(file_to_post_id.values())
    stego_by_file: list[tuple[Path, str, Counter]] = []
    skipped_no_stego = 0
    skipped_empty_stego = 0
    total = len(output_files)
    for idx, file_path in enumerate(output_files, start=1):
        post_id = file_to_post_id[file_path]
        stego_text = extract_stego_text_divergence(file_path)
        if stego_text is None:
            skipped_no_stego += 1
            _maybe_progress(hook, "Loading stego samples", idx, total)
            continue
        stego_counter = Counter(tokenize(stego_text))
        if not stego_counter:
            skipped_empty_stego += 1
            _maybe_progress(hook, "Loading stego samples", idx, total)
            continue
        stego_by_file.append((file_path, post_id, stego_counter))
        _maybe_progress(hook, "Loading stego samples", idx, total)
    skips = {
        "skipped_no_stego": skipped_no_stego,
        "skipped_empty_stego": skipped_empty_stego,
    }
    return stego_by_file, output_post_ids, skips, len(output_files)


def run_divergence_metrics(
    output_dir: Path,
    dataset_dir: Path,
    metrics_dir: Path,
    *,
    alpha: float = 1e-6,
    progress_hook: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Compute KL/JSD of stego vs matched-post and global comment baselines; save JSON report."""
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output_dir not found or not a directory: {output_dir}")
    if not dataset_dir.is_dir():
        raise FileNotFoundError(f"dataset_dir not found or not a directory: {dataset_dir}")
    stego_by_file, output_post_ids, stego_skips, n_output_files = _collect_divergence_samples(
        output_dir, progress_hook
    )
    if not stego_by_file:
        raise ValueError("No valid stego samples were found; cannot compute divergences.")
    primary_counters = load_primary_counters(dataset_dir, output_post_ids, progress_hook)
    dataset_post_file_count, global_counter, global_nonempty_bodies = load_global_stats(
        dataset_dir, progress_hook
    )
    primary_stats = evaluate_baseline(
        stego_by_file, primary_counters, None, alpha, progress_hook, primary=True
    )
    secondary_stats = evaluate_baseline(
        stego_by_file, None, global_counter, alpha, progress_hook, primary=False
    )
    skipped_no_primary = 0
    skipped_empty_primary = 0
    for _, post_id, _ in stego_by_file:
        if post_id not in primary_counters:
            skipped_no_primary += 1
        elif not primary_counters[post_id]:
            skipped_empty_primary += 1
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "output_dir": str(output_dir.resolve()),
            "dataset_dir": str(dataset_dir.resolve()),
            "metrics_dir": str(metrics_dir.resolve()),
            "tokenization": "word_unigram",
            "token_regex": TOKEN_RE.pattern,
            "smoothing_alpha": alpha,
            "kl_direction": "KL(stego||baseline)",
        },
        "dataset_summary": {
            "total_output_files": n_output_files,
            "unique_output_post_ids": len(output_post_ids),
            "usable_stego_samples": len(stego_by_file),
            "skipped_missing_stegoText": stego_skips["skipped_no_stego"],
            "skipped_empty_stego_tokens": stego_skips["skipped_empty_stego"],
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
    report_path = save_divergence_report(metrics_dir, report)
    return {"report": report, "report_path": str(report_path.resolve())}


def _perplexity_one_text(
    stego_text: str,
    model_name: str,
    stride: int,
    device: str,
) -> tuple[float | None, str | None, str | None]:
    """Returns (perplexity, resolved_device, warning). Warning set if deps missing or score invalid."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        return None, None, f"Perplexity skipped: missing transformers/torch ({exc})."
    resolved = resolve_device(torch, device)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    causal_lm = cast(Any, AutoModelForCausalLM.from_pretrained(model_name))
    causal_lm.to(resolved)
    causal_lm.eval()
    max_length = int(getattr(causal_lm.config, "n_positions", 1024))
    ppl = compute_text_perplexity(
        tokenizer,
        causal_lm,
        torch,
        stego_text,
        stride,
        max_length,
        resolved,
    )
    if not math.isfinite(ppl):
        return None, resolved, "Perplexity unavailable (sequence too short for scoring)."
    return ppl, resolved, None


def _kl_jsd_pair(
    stego_counter: Counter,
    baseline: Counter | None,
    alpha: float,
) -> tuple[float | None, float | None]:
    if baseline is None or not baseline or not stego_counter:
        return None, None
    kl = kl_divergence(stego_counter, baseline, alpha)
    jsd = js_divergence(stego_counter, baseline, alpha)
    return _json_number(kl), _json_number(jsd)


def run_single_post_metrics(
    output_file: Path,
    dataset_dir: Path,
    *,
    model_name: str = "gpt2",
    stride: int = 512,
    device: str = "auto",
    alpha: float = 1e-6,
    progress_hook: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Perplexity + KL/JSD for one pipeline output JSON; no report file written."""
    if stride <= 0:
        raise ValueError("stride must be a positive integer")
    if alpha <= 0:
        raise ValueError("alpha must be positive")
    if not output_file.is_file():
        raise FileNotFoundError(f"Output file not found: {output_file}")
    if not dataset_dir.is_dir():
        raise FileNotFoundError(f"dataset_dir not found or not a directory: {dataset_dir}")
    try:
        payload = json.loads(output_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in output file: {exc}") from exc
    stego_text = extract_stego_text_unified(payload)
    if stego_text is None:
        raise ValueError("No stego text found (stegoText / stego_text).")
    if not stego_text.strip():
        raise ValueError("Stego text is empty.")
    post_id = output_file.stem.split("_version_")[0]
    stego_counter = Counter(tokenize(stego_text))
    warnings: list[str] = []
    if not stego_counter:
        warnings.append("Stego text produced zero word unigrams; KL/JSD omitted.")
    _, global_counter, _ = load_global_stats(dataset_dir, progress_hook)
    primary_path = dataset_dir / f"{post_id}.json"
    primary_counter: Counter | None = None
    if primary_path.is_file():
        primary_counter = extract_comment_counter(primary_path)
        if not primary_counter:
            warnings.append("Primary baseline: matched post has no comment tokens.")
    else:
        warnings.append(f"Primary baseline: missing dataset file {post_id}.json")
    ppl, resolved_dev, ppl_warn = _perplexity_one_text(
        stego_text, model_name, stride, device
    )
    if ppl_warn:
        warnings.append(ppl_warn)
    kl_p, jsd_p = _kl_jsd_pair(stego_counter, primary_counter, alpha)
    kl_g, jsd_g = _kl_jsd_pair(stego_counter, global_counter, alpha)
    if not global_counter:
        warnings.append("Global baseline has no comment tokens in dataset_dir.")
    rel_file = _repo_relative_path(output_file.resolve(), REPO_ROOT)
    primary_block: dict[str, Any] | None = None
    if primary_path.is_file():
        primary_block = {
            "matched_post_file": _repo_relative_path(primary_path.resolve(), REPO_ROOT),
            "kl_stego_vs_matched_post": kl_p,
            "jsd_stego_vs_matched_post": jsd_p,
        }
    secondary_block: dict[str, Any] | None = None
    if kl_g is not None or jsd_g is not None:
        secondary_block = {
            "kl_stego_vs_global_corpus": kl_g,
            "jsd_stego_vs_global_corpus": jsd_g,
        }
    return {
        "file": rel_file,
        "post_id": post_id,
        "perplexity": _json_number(ppl) if ppl is not None else None,
        "resolved_device": resolved_dev,
        "primary_baseline_matched_post": primary_block,
        "secondary_baseline_global_corpus": secondary_block,
        "warnings": warnings,
        "config": {
            "model_name": model_name,
            "stride": stride,
            "device_requested": device,
            "smoothing_alpha": alpha,
            "tokenization": "word_unigram",
            "token_regex": TOKEN_RE.pattern,
            "kl_direction": "KL(stego||baseline)",
        },
    }


def _repo_relative_path(absolute_path: Path, repo_root: Path) -> str:
    try:
        rel = absolute_path.resolve().relative_to(repo_root.resolve())
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(absolute_path.resolve())


def _metrics_file_row(path: Path, kind: str, repo_root: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "kind": kind,
        "filename": path.name,
        "path": _repo_relative_path(path, repo_root),
        "size_bytes": st.st_size,
        "updated_at_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def _append_glob_rows(
    acc: list[tuple[int, dict[str, Any]]],
    metrics_dir: Path,
    glob_pattern: str,
    kind: str,
    repo_root: Path,
) -> None:
    for path in metrics_dir.glob(glob_pattern):
        if path.is_file():
            st = path.stat()
            acc.append((st.st_mtime_ns, _metrics_file_row(path, kind, repo_root)))


def list_metrics_history(
    metrics_dir: Path,
    *,
    kind_filter: str = "all",
    limit: int = 50,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Newest-first list of saved perplexity/divergence JSON reports under metrics_dir."""
    root = repo_root if repo_root is not None else REPO_ROOT
    if not metrics_dir.exists():
        return []
    if not metrics_dir.is_dir():
        raise ValueError(f"metrics_dir is not a directory: {metrics_dir}")
    cap = max(1, min(int(limit), 500))
    rows: list[tuple[int, dict[str, Any]]] = []
    if kind_filter in ("all", "perplexity"):
        _append_glob_rows(rows, metrics_dir, "perplexity_metrics_*.json", "perplexity", root)
    if kind_filter in ("all", "divergence"):
        _append_glob_rows(rows, metrics_dir, "divergence_metrics_*.json", "divergence", root)
    rows.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in rows[:cap]]
