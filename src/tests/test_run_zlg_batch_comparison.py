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
