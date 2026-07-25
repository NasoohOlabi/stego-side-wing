from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch, raises


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_publication_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_publication_benchmark", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(method: str, post_id: str, *, accepted: bool = True) -> dict:
    return {
        "post_id": post_id,
        "method": method,
        "accepted": accepted,
        "decode_ok": accepted,
        "payload_bits_encoded": 64 if accepted else 0,
        "transformed_payload_bits": 64,
        "protocol_overhead_bits": 4,
        "total_embedded_bits": 68 if accepted else 4,
    }


def test_payload_is_deterministic_and_exactly_64_bits() -> None:
    module = _load_module()

    payload = module._payload("post-1", 123)

    assert payload == module._payload("post-1", 123)
    assert len(payload.encode("utf-8")) * 8 == 64


def test_pilot_gate_requires_complete_paired_attempts() -> None:
    module = _load_module()
    rows = [_row(method, f"p{index}") for index in range(25) for method in module.METHODS]

    passing = module._summary(rows, 25)
    incomplete = module._summary(rows[:-1], 25)

    assert passing["expansion_gate_passed"] is True
    assert incomplete["expansion_gate_passed"] is False


def test_pilot_gate_counts_generation_failures() -> None:
    module = _load_module()
    rows = [
        _row(method, f"p{index}", accepted=index >= 6)
        for index in range(25)
        for method in module.METHODS
    ]

    summary = module._summary(rows, 25)

    assert summary["methods"]["our_method"]["generation_success_rate"] == 0.76
    assert summary["expansion_gate_passed"] is False


def test_completed_run_resume_filters_pilot_to_first_25_posts() -> None:
    module = _load_module()
    rows = [_row(method, f"p{index}") for index in range(100) for method in module.METHODS]

    pilot = module._rows_for_posts(rows, [f"p{index}" for index in range(25)])

    assert module._summary(pilot, 25)["expansion_gate_passed"] is True


def test_resume_ignores_rows_from_a_different_run_signature(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / "results.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"post_id": "p1", "method": "our_method", "run_signature": "current"},
                {"post_id": "p2", "method": "official_zgls", "run_signature": "stale"},
            )
        )
        + "\n",
        encoding="utf-8",
    )

    assert module._load_done(path, "current") == {("p1", "our_method")}


def test_run_signature_covers_comparison_configuration() -> None:
    module = _load_module()
    manifest = {"manifest_id": "frozen"}
    base = SimpleNamespace(
        comparison_mode="capacity_matched",
        zlg_server_url="http://127.0.0.1:9000",
        max_carriers=8,
        max_total_words=320,
        max_retries=3,
        zlg_max_new_tokens=640,
        allow_dirty=False,
        zlg_server_identity={"status": "ok", "model": "qwen-test"},
    )
    changed = SimpleNamespace(**{**vars(base), "max_total_words": 321})

    assert module._run_signature(manifest, base) != module._run_signature(manifest, changed)


def test_zlg_server_identity_requires_and_records_a_version(
    monkeypatch: MonkeyPatch,
) -> None:
    module = _load_module()

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "ok", "model": "Qwen3.5-9B-Q4_K_M.gguf"}

    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: _Response())

    with raises(ValueError, match="server version"):
        module._zlg_server_identity("http://127.0.0.1:9000")
    identity = module._zlg_server_identity("http://127.0.0.1:9000", "commit-123")

    assert identity["benchmark_server_version"] == "commit-123"


def test_max_capacity_uses_zlg_probe_instead_of_target_frames(
    monkeypatch: MonkeyPatch,
) -> None:
    module = _load_module()
    seen = []

    def _probe(sample: Any) -> dict[str, Any]:
        seen.append(sample)
        return {
            "accepted": True,
            "decode_ok": True,
            "payload_bits_encoded": 96,
            "protocol_overhead_bits": 16,
            "total_embedded_bits": 112,
            "capacity_best_success": {
                "stegotext": "A verified maximum capacity carrier.",
                "decode_ok": True,
                "secret": "capacity-secret",
            },
        }

    monkeypatch.setattr(module, "run_comparison_sample", _probe)
    monkeypatch.setattr(
        module,
        "run_comparison_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("wrong lane")),
    )
    args = SimpleNamespace(
        zlg_server_url="http://127.0.0.1:9000",
        comparison_mode="max_capacity",
        max_retries=3,
        max_carriers=8,
        max_total_words=320,
        zlg_max_new_tokens=640,
    )

    result = module._run_zlg(
        {"comments": [{"body": "One suitable real source comment."}]},
        "12345678",
        args,
        7,
    )

    assert len(seen) == 8
    assert seen[0].use_capacity_probe is True
    assert seen[0].quality_max_words == 320
    assert seen[0].quality_max_retries == 3
    assert seen[0].max_new_tokens == 640
    assert result["accepted"] is True
    assert result["payload_bits_target"] == 768
    assert result["payload_bits_encoded"] == 768
    assert result["carrier_count"] == 8
    assert result["capacity_probe_ceiling_bits"] == 2048
    assert result["capacity_censored"] is False


def test_failed_max_capacity_probe_has_no_carrier(monkeypatch: MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.setattr(
        module,
        "run_comparison_sample",
        lambda sample: {
            "accepted": False,
            "decode_ok": False,
            "reason": "capacity_probe_no_clean_success",
            "payload_bits_encoded": 0,
        },
    )
    args = SimpleNamespace(
        zlg_server_url="http://127.0.0.1:9000",
        comparison_mode="max_capacity",
        max_retries=3,
        max_carriers=8,
        max_total_words=320,
        zlg_max_new_tokens=640,
    )

    result = module._run_zlg(
        {"comments": [{"body": "One suitable real source comment."}]},
        "12345678",
        args,
        7,
    )

    assert result["accepted"] is False
    assert result["carrier_count"] == 0
    assert result["frames"] == []


def test_capacity_probe_selects_highest_verified_trial_within_word_budget() -> None:
    module = _load_module()
    result = {
        "accepted": True,
        "decode_ok": True,
        "payload_bits_encoded": 256,
        "protocol_overhead_bits": 16,
        "total_embedded_bits": 272,
        "capacity_trials": [
            {
                "success": True,
                "quality_passed": True,
                "decode_ok": True,
                "payload_bits_exact": 64,
                "header_bits": 16,
                "total_used_bits": 80,
                "stegotext": "A short verified carrier.",
            },
            {
                "success": False,
                "decode_ok": False,
                "payload_bits_exact": 96,
                "stegotext": "A failed candidate must not inherit decode success.",
            },
        ],
        "capacity_best_success": {
            "success": True,
            "quality_passed": True,
            "decode_ok": True,
            "payload_bits_exact": 256,
            "header_bits": 16,
            "total_used_bits": 272,
            "stegotext": "word " * 20,
        },
    }

    frame = module._capacity_frame(result, 10)

    assert frame is not None
    assert frame["payload_bits_encoded"] == 64
    assert frame["total_embedded_bits"] == 80


def test_our_max_payload_is_bounded_by_dynamic_selection_capacity(
    monkeypatch: MonkeyPatch,
) -> None:
    module = _load_module()

    class _Pipeline:
        def plan_payload_frames(
            self, payload: str, posts: list[dict[str, Any]], max_frames_per_post: int
        ) -> dict[str, bool]:
            assert max_frames_per_post == 2
            return {"succeeded": len(payload.encode("utf-8")) <= 3}

    monkeypatch.setattr(
        module,
        "selection_channel_capacity_report",
        lambda post: {"recoverable_capacity_bits": 20},
    )
    monkeypatch.setattr(module, "StegoPipeline", _Pipeline)

    payload = module._max_our_payload({}, "post-1", 7, 2)

    assert len(payload.encode("utf-8")) == 3


def test_our_max_capacity_falls_back_to_verified_smaller_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_our_capacity_payloads", lambda *args, **kwargs: ["four", "two"])

    def _run(
        post: dict[str, Any],
        payload: str,
        max_carriers: int,
        max_total_words: int,
        max_retries: int,
    ) -> dict[str, Any]:
        accepted = payload == "two"
        return {
            "accepted": accepted,
            "decode_ok": accepted,
            "reason": None if accepted else "generation_failed",
            "carrier_count": 1,
            "word_count": 4,
            "payload_bits_encoded": len(payload.encode("utf-8")) * 8 if accepted else 0,
        }

    monkeypatch.setattr(module, "_run_our_method", _run)

    result, payload = module._run_our_max_capacity({}, "p1", 7, 8, 320, 3)

    assert payload == "two"
    assert result["accepted"] is True
    assert [trial["accepted"] for trial in result["capacity_trials"]] == [False, True]
