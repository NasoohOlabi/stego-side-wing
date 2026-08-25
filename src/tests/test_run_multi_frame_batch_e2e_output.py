"""Frame output rows must match the Artifact Explorer single-frame contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_multi_frame_batch_e2e.py"
    spec = importlib.util.spec_from_file_location("run_multi_frame_batch_e2e", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frame_output_rows_include_angle_embedding_and_search_context() -> None:
    module = _load_module()
    post = {
        "id": "abc123",
        "title": "Example",
        "author": "op",
        "selftext": "body",
        "permalink": "/r/news/comments/abc123/example/",
        "url": "https://example.test/story",
        "search_results": ["research snippet one", "research snippet two"],
        "comments": [
            {
                "id": "c1",
                "body": "parent comment",
                "author": "u1",
                "replies": [],
            }
        ],
        "angles": [
            {
                "category": "food",
                "source_quote": "quote",
                "tangent": "talk about dyes",
                "idx": 0,
            }
        ],
    }
    frame = {
        "parent_id": "c1",
        "parent_recoverable_width": 3,
        "tangent_recoverable_width": 2,
        "context_dictionary_id": "dict-1",
        "tangent_hash": "th",
        "selected_angle_index": 0,
        "stego_text": "cover text",
        "sender_audit": {"selected_angle_index": 0},
        "embedding_plan": {
            "commentEmbedding": {
                "bitsUsed": "010",
                "bitsCount": 3,
                "recoverableBitsCount": 3,
            },
            "angleEmbedding": {
                "bitsUsed": "0",
                "bitsCount": 1,
                "selectedAngle": {
                    "category": "food",
                    "source_quote": "quote",
                    "tangent": "talk about dyes",
                    "idx": 0,
                },
                "remainingAngles": [],
                "totalAnglesSelectedFirst": [
                    {
                        "category": "food",
                        "source_quote": "quote",
                        "tangent": "talk about dyes",
                        "idx": 0,
                    }
                ],
                "TangentsDB": [
                    {
                        "category": "food",
                        "source_quote": "quote",
                        "tangent": "talk about dyes",
                        "idx": 0,
                    }
                ],
            },
        },
    }

    rows = module._frame_output_rows(frame, post, payload="secret", frame_bits=5)
    assert len(rows) == 1
    item = rows[0]
    assert item["post"]["url"] == "https://example.test/story"
    assert item["post"]["search_results"] == ["research snippet one", "research snippet two"]
    angle_embedding = item["embedding"]["angleEmbedding"]
    assert len(angle_embedding["totalAnglesSelectedFirst"]) == 1
    assert angle_embedding["selectedAngle"]["idx"] == 0
    assert item["embedding"]["commentEmbedding"]["bitsUsed"] == "010"
    assert item["embedding"]["commentEmbedding"]["bitsCount"] == 5
    assert item["embedding"]["multiFrame"]["selectedAngleIndex"] == 0


def test_context_block_emits_empty_search_results_when_present() -> None:
    module = _load_module()
    block = module._context_block(
        {
            "id": "x",
            "title": "t",
            "author": "a",
            "selftext": "",
            "permalink": "/r/x/",
            "url": "https://example.test",
            "search_results": [],
        }
    )
    assert block["url"] == "https://example.test"
    assert block["search_results"] == []
