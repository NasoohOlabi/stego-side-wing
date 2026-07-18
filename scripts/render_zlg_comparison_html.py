from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _fmt(value: Any, digits: int = 3) -> str:
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    if value is None:
        return "-"
    return html.escape(str(value))


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _method_title(name: str) -> str:
    return "Our Method" if name == "our_method" else "ZLG"


def _method_block(summary: dict[str, Any], method: str) -> dict[str, Any]:
    methods = summary.get("methods") if isinstance(summary.get("methods"), dict) else {}
    block = methods.get(method)
    return block if isinstance(block, dict) else {}


def _method_cards(summary: dict[str, Any]) -> str:
    methods = summary.get("methods") if isinstance(summary.get("methods"), dict) else {}
    cards = []
    for name in ("our_method", "zlg"):
        block = methods.get(name)
        if not isinstance(block, dict):
            continue
        cards.append(
            f"""
            <section class="card">
              <h2>{_method_title(str(name))}</h2>
              <dl>
                <dt>valid samples</dt><dd>{_fmt(block.get("n"))}</dd>
                <dt class="primary">message bits carried</dt><dd class="primary">{_fmt(block.get("payload_bits_encoded_mean"))}</dd>
                <dt>comment length</dt><dd>{_fmt(block.get("word_count_mean"))} words</dd>
                <dt>fluency score</dt><dd>{_fmt(block.get("perplexity_gpt2_mean"))} PPL</dd>
                <dt>corpus drift</dt><dd>{_fmt(block.get("kl_global_corpus_mean"))} KLD</dd>
                <dt>bounded drift</dt><dd>{_fmt(block.get("jsd_global_corpus_mean"))} JSD</dd>
                <dt class="diagnostic">protocol overhead</dt><dd class="diagnostic">{_fmt(block.get("protocol_overhead_bits_mean"))} bits</dd>
                <dt class="diagnostic">wire bits total</dt><dd class="diagnostic">{_fmt(block.get("total_embedded_bits_mean"))}</dd>
              </dl>
            </section>
            """
        )
    return "\n".join(cards)


def _metric_notes(summary: dict[str, Any], progress: dict[str, Any]) -> str:
    our = _method_block(summary, "our_method")
    zlg = _method_block(summary, "zlg")
    paired = (
        summary.get("paired_statistics")
        if isinstance(summary.get("paired_statistics"), dict)
        else {}
    )
    paired_n = paired.get("paired_n")
    zlg_payload = _num(zlg.get("payload_bits_encoded_mean"))
    our_payload = _num(our.get("payload_bits_encoded_mean"))
    zlg_words = _num(zlg.get("word_count_mean"))
    our_words = _num(our.get("word_count_mean"))
    zlg_ppl = _num(zlg.get("perplexity_gpt2_mean"))
    our_ppl = _num(our.get("perplexity_gpt2_mean"))
    zlg_kld = _num(zlg.get("kl_global_corpus_mean"))
    our_kld = _num(our.get("kl_global_corpus_mean"))
    payload_delta = (
        None if zlg_payload is None or our_payload is None else zlg_payload - our_payload
    )
    word_delta = None if zlg_words is None or our_words is None else zlg_words - our_words
    ppl_note = "Lower PPL usually means the text is easier for GPT-2 to predict, so it is a rough fluency check."
    if zlg_ppl is not None and our_ppl is not None:
        ppl_note = f"Lower PPL is better; here ZLG is {_fmt(abs(zlg_ppl - our_ppl))} {'lower' if zlg_ppl < our_ppl else 'higher'} than our method."
    kld_note = "Lower KLD/JSD means the word distribution is closer to the reference corpus."
    if zlg_kld is not None and our_kld is not None:
        kld_note = f"Lower KLD is better; here ZLG is {_fmt(abs(zlg_kld - our_kld))} {'lower' if zlg_kld < our_kld else 'higher'} than our method."
    return f"""
    <section class="card explainer">
      <h2>Plain-English Summary</h2>
      <p>This page compares two steganography methods on the same source posts, one row pair at a time.</p>
      <p><strong>Message bits carried</strong> is the main capacity number: it counts recovered secret payload bits inside a short comment.</p>
      <p>The current report has <strong>{_fmt(paired_n)}</strong> valid paired samples from <strong>{_fmt(progress.get("processed_now"))}/{_fmt(progress.get("total_entries"))}</strong> processed API attempts.</p>
      <p>So far, ZLG carries <strong>{_fmt(zlg_payload)}</strong> message bits on average, versus <strong>{_fmt(our_payload)}</strong> for our method; the mean difference is <strong>{_fmt(payload_delta)}</strong> bits.</p>
      <p>ZLG comments average <strong>{_fmt(zlg_words)}</strong> words, versus <strong>{_fmt(our_words)}</strong> words for our method; the mean length difference is <strong>{_fmt(word_delta)}</strong> words.</p>
      <p>{ppl_note}</p>
      <p>{kld_note}</p>
      <p><strong>Protocol overhead</strong> and <strong>wire bits total</strong> are diagnostic implementation details; they are not the headline capacity comparison.</p>
    </section>
    """


_DELTA_LABELS = {
    "payload_bits_encoded": "message bits carried",
    "total_embedded_bits": "wire bits total",
    "perplexity_gpt2": "fluency score (PPL)",
    "kl_global_corpus": "corpus drift (KLD)",
    "jsd_global_corpus": "bounded drift (JSD)",
    "word_count": "comment length",
    "repetition_ratio": "repetition ratio",
}


def _paired_table(summary: dict[str, Any]) -> str:
    stats = (
        summary.get("paired_statistics")
        if isinstance(summary.get("paired_statistics"), dict)
        else {}
    )
    rows = []
    for key in _DELTA_LABELS:
        block = stats.get(key)
        if not isinstance(block, dict):
            continue
        rows.append(
            f"""
            <tr>
              <td>{html.escape(_DELTA_LABELS[key])}</td>
              <td>{_fmt(block.get("n"))}</td>
              <td>{_fmt(block.get("mean_delta_zlg_minus_our"))}</td>
              <td>{_fmt(block.get("median_delta_zlg_minus_our"))}</td>
              <td>{_fmt(block.get("zlg_greater_count"))}</td>
              <td>{_fmt(block.get("our_greater_count"))}</td>
              <td>{_fmt(block.get("two_sided_sign_test_p"), 8)}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def _row_value(row: dict[str, Any] | None, key: str, digits: int = 3) -> str:
    if not row:
        return "-"
    return _fmt(row.get(key), digits)


def _grouped_sample_rows(rows: list[dict[str, Any]], limit: int | None) -> str:
    pairs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        post_id = str(row.get("post_id") or "")
        sample_index = str(row.get("sample_index") or "")
        key = (post_id, sample_index)
        if key not in pairs:
            pairs[key] = {}
            order.append(key)
        method = str(row.get("method") or "")
        pairs[key][method] = row
    if limit is not None:
        order = order[-limit:]
    out = []
    for post_id, sample_index in order:
        pair = pairs[(post_id, sample_index)]
        zlg = pair.get("zlg")
        ours = pair.get("our_method")
        zlg_text = html.escape(str((zlg or {}).get("stegotext") or ""))
        our_text = html.escape(str((ours or {}).get("stegotext") or ""))
        out.append(
            f"""
            <tbody class="sample-group">
            <tr class="sample-head">
              <th colspan="2">Post {html.escape(post_id)} · Sample {html.escape(sample_index)}</th>
            </tr>
            <tr>
              <th>ZLG</th>
              <th>Our Method</th>
            </tr>
            <tr>
              <td>
                <dl class="inline-metrics">
                  <dt>payload bits</dt><dd>{_row_value(zlg, "payload_bits_encoded")}</dd>
                  <dt>overhead</dt><dd>{_row_value(zlg, "protocol_overhead_bits")}</dd>
                  <dt>wire total</dt><dd>{_row_value(zlg, "total_embedded_bits")}</dd>
                  <dt>fluency PPL</dt><dd>{_row_value(zlg, "perplexity_gpt2")}</dd>
                  <dt>corpus KLD</dt><dd>{_row_value(zlg, "kl_global_corpus")}</dd>
                </dl>
              </td>
              <td>
                <dl class="inline-metrics">
                  <dt>payload bits</dt><dd>{_row_value(ours, "payload_bits_encoded")}</dd>
                  <dt>overhead</dt><dd>{_row_value(ours, "protocol_overhead_bits")}</dd>
                  <dt>wire total</dt><dd>{_row_value(ours, "total_embedded_bits")}</dd>
                  <dt>fluency PPL</dt><dd>{_row_value(ours, "perplexity_gpt2")}</dd>
                  <dt>corpus KLD</dt><dd>{_row_value(ours, "kl_global_corpus")}</dd>
                </dl>
              </td>
            </tr>
            <tr>
              <td class="text">{zlg_text}</td>
              <td class="text">{our_text}</td>
            </tr>
            </tbody>
            """
        )
    return "\n".join(out)


def render(run_dir: Path, output: Path, limit: int) -> Path:
    dataset_dir = run_dir / "comparison_dataset"
    summary = _load_json(dataset_dir / "summary.json")
    progress = _load_json(run_dir / "progress.json")
    rows = _load_jsonl(dataset_dir / "paired_rows.jsonl")
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="120">
  <title>ZLG vs Our Method Comparison</title>
  <style>
    :root {{ color-scheme: light; --ink:#1f2933; --muted:#5f6b7a; --line:#d8dee7; --bg:#f7f8fb; --panel:#fff; --accent:#0f766e; --soft:#ecfdf5; }}
    body {{ margin:0; font:14px/1.45 system-ui, Segoe UI, sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:24px 28px 12px; border-bottom:1px solid var(--line); background:var(--panel); }}
    h1 {{ margin:0 0 8px; font-size:24px; }}
    h2 {{ margin:0 0 12px; font-size:16px; }}
    main {{ padding:20px 28px 36px; }}
    .meta {{ color:var(--muted); display:flex; gap:18px; flex-wrap:wrap; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; margin-bottom:20px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .explainer {{ background:var(--soft); border-color:#a7f3d0; }}
    .explainer p {{ margin:0 0 8px; max-width:78ch; }}
    dl {{ display:grid; grid-template-columns:1fr auto; gap:7px 12px; margin:0; }}
    dt {{ color:var(--muted); }}
    dd {{ margin:0; font-variant-numeric:tabular-nums; }}
    .primary {{ color:#0f766e; font-weight:700; }}
    .diagnostic {{ color:#7c8797; }}
    table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); margin:0 0 20px; }}
    th, td {{ padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
    th {{ text-align:left; color:var(--muted); font-weight:600; background:#fbfcfe; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .text {{ max-width:680px; }}
    .note {{ color:var(--muted); margin:0 0 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>ZLG vs Our Method Comparison</h1>
    <div class="meta">
      <span>Processed: {_fmt(progress.get("processed_now"))}/{_fmt(progress.get("total_entries"))}</span>
      <span>Accepted ZLG rows: {_fmt(progress.get("accepted_now"))}</span>
      <span>Paired samples: {_fmt((summary.get("paired_statistics") or {}).get("paired_n"))}</span>
      <span>Updated: {_fmt(progress.get("updated_at_utc") or summary.get("updated_at_utc"))}</span>
    </div>
  </header>
  <main>
    <div class="grid">{_method_cards(summary)}{_metric_notes(summary, progress)}</div>
    <h2>Paired Deltas</h2>
    <p class="note">Delta is ZLG minus our method. Positive means ZLG is higher. The p-value is not meaningful until there are many paired samples.</p>
    <table>
      <thead><tr><th>Metric</th><th>n</th><th>Mean delta</th><th>Median delta</th><th>ZLG greater</th><th>Our greater</th><th>p</th></tr></thead>
      <tbody>{_paired_table(summary)}</tbody>
    </table>
    <h2>All Paired Samples</h2>
    <table class="samples">
      {_grouped_sample_rows(rows, None if limit <= 0 else limit)}
    </table>
  </main>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_doc, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Render ZLG comparison HTML report.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--limit", type=int, default=0, help="Number of paired sample groups to show. 0 means all."
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    output = (
        Path(args.output).resolve()
        if args.output
        else run_dir / "comparison_dataset" / "index.html"
    )
    print(render(run_dir, output, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
