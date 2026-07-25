from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_attack_receivers.py"
    spec = importlib.util.spec_from_file_location("run_attack_receivers", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replace_comment_finds_nested_sender_frame() -> None:
    module = _load_module()
    comments = [
        {"id": "root", "body": "root", "replies": [{"id": "frame", "body": "old", "replies": []}]}
    ]

    replaced = module._replace_comment(comments, "frame", "attacked")

    assert replaced is True
    assert comments[0]["replies"][0]["body"] == "attacked"


def test_not_applicable_attack_skips_receiver() -> None:
    module = _load_module()

    result = module.evaluate({"applicable": False})

    assert result["decode_ok"] is None
    assert result["decode_reason"] == "not_applicable"


def test_zlg_context_mutation_removes_exactly_one_original_example() -> None:
    module = _load_module()
    prompt = "Header\n- first\n- second\n- third\nFooter"

    mutated = module._mutate_zlg_prompt(prompt, 1)

    assert mutated == "Header\n- first\n- third\nFooter"
