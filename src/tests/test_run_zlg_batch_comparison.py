from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_zlg_batch_comparison.py"
    spec = importlib.util.spec_from_file_location("run_zlg_batch_comparison", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extract_sample_reads_payload_and_context(tmp_path: Path) -> None:
    mod = _load_module()
    out = tmp_path / "sample.json"
    payload = [
        {
            "embedding": {
                "compression": {"payload": "hello"},
                "commentEmbedding": {
                    "context": {"title": "t", "permalink": "/p", "selftext": "s"},
                    "pickedCommentChain": [{"name": "u", "body": "b"}],
                },
            }
        }
    ]
    out.write_text(json.dumps(payload), encoding="utf-8")
    cover_texts, secret, embedded_bits = mod._extract_sample(
        {"output_file": str(out)}, max_selftext_chars=2000, max_chain_chars=1000
    )
    assert any("t" in sentence for sentence in cover_texts)
    assert any("b" in sentence for sentence in cover_texts)
    assert secret == "hello"
    assert embedded_bits == 0


def test_single_comment_sentence_is_topped_up_from_post_context() -> None:
    """A lone usable comment sentence must not strand the sample (was 94/554 of a run)."""
    mod = _load_module()
    context = {
        "title": "State appeals the EdChoice ruling in court.",
        "selftext": "Funding for private schools is now under review. Districts are worried.",
    }
    picked = [{"name": "u", "body": "Short. The state is actively appealing that ruling."}]

    cover = mod._build_cover_texts(context, picked, 2500, 1400)

    assert len(cover) >= mod.MIN_COVER_TEXTS
    # The comment sentence still leads; context only backfills.
    assert cover[0] == "The state is actively appealing that ruling."


def test_sufficient_comment_sentences_do_not_pull_in_post_context() -> None:
    mod = _load_module()
    context = {"title": "A title that should stay out.", "selftext": "Body text."}
    picked = [{"name": "u", "body": "First real comment sentence. Second real comment sentence."}]

    cover = mod._build_cover_texts(context, picked, 2500, 1400)

    assert cover == ["First real comment sentence.", "Second real comment sentence."]


def test_load_processed_uses_source_key(tmp_path: Path) -> None:
    mod = _load_module()
    p = tmp_path / "results.jsonl"
    p.write_text('{"source_key":"a|1|h|f"}\n{"x":1}\n', encoding="utf-8")
    done = mod._load_processed(p)
    assert "a|1|h|f" in done


def test_load_existing_counts(tmp_path: Path) -> None:
    mod = _load_module()
    p = tmp_path / "results.jsonl"
    p.write_text(
        '{"source_key":"a","accepted":true}\n'
        '{"source_key":"b","accepted":false}\n'
        '{"source_key":"c"}\n',
        encoding="utf-8",
    )
    processed, accepted, failed = mod._load_existing_counts(p)
    assert processed == 3
    assert accepted == 1
    assert failed == 2


def test_capacity_matched_mode_uses_one_explicit_payload_target() -> None:
    mod = _load_module()

    assert mod._payload_candidates_for_mode("capacity_matched", 21, ()) == (21,)
    assert (
        mod._payload_candidates_for_mode("max_capacity", 21, ())
        == mod.ComparisonInput.payload_bits_candidates
    )
    assert mod._payload_candidates_for_mode("capacity_matched", 21, (8, 16)) == (8, 16)
