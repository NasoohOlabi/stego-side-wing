from __future__ import annotations

import importlib.util
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_suspiciousness_parser_requires_valid_json_index() -> None:
    module = _load("run_suspiciousness_judge")

    assert module._selected_index('{"suspicious_index": 2}') == 2
    assert module._selected_index('{"suspicious_index": 4}') is None
    assert module._selected_index("candidate 2") is None


def test_paraphrase_parser_preserves_plain_text_and_json_text() -> None:
    module = _load("run_paraphrase_attacks")

    assert module._text('{"text": "rewritten"}') == "rewritten"
    assert module._text("plain rewrite") == "plain rewrite"
