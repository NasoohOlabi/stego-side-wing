from __future__ import annotations

from pathlib import Path

from services import zlg_comparison_service as svc


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def json(self) -> dict:
        return self._payload


def test_build_prompt_includes_domain_and_chain() -> None:
    prompt = svc.build_prompt("Example Corpus", ["Sentence A.", "Sentence B."], seed=7, n_cover=2)
    assert "<CORPUS>Example Corpus</CORPUS>" in prompt
    assert "Sentence A." in prompt
    assert "Sentence B." in prompt
    assert "<OUTPUT>" in prompt


def test_prompt_leakage_detector_flags_template_artifacts() -> None:
    assert svc.stegotext_has_prompt_leakage("</OUTPUT> broken")
    assert svc.stegotext_has_prompt_leakage("Wait, something is wrong.")
    assert not svc.stegotext_has_prompt_leakage("This seems like a normal sentence.")


def test_build_api_prompt_is_plain_for_http_service() -> None:
    prompt = svc.build_api_prompt("Reddit news discussion")
    assert "Reddit news discussion" in prompt
    assert "<OUTPUT>" not in prompt
    assert "[INST]" not in prompt


def test_run_comparison_success_with_reveal(monkeypatch) -> None:
    calls: list[dict] = []

    def _fake_post(url: str, json: dict, timeout: int):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/hide"):
            return _FakeResponse(
                {
                    "stegotext": "stego text",
                    "payload_bytes": 5,
                    "is_truncated": False,
                    "ppl": 12.5,
                    "params_used": {"mode": "huffman"},
                }
            )
        return _FakeResponse({"decode_ok": True, "secret": "hello"})

    monkeypatch.setattr(svc.requests, "post", _fake_post)
    result = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two.", "Sentence three."],
            max_retries=2,
        )
    )

    assert result["accepted"] is True
    assert result["payload_bytes_target"] == 5
    assert result["payload_bytes_actual"] == 5
    assert result["decode_ok"] is True
    hide_body = calls[0]["json"]
    assert hide_body["complete_sent"] is True


def test_run_comparison_retries_and_fails_on_size_mismatch(monkeypatch) -> None:
    def _fake_post(url: str, json: dict, timeout: int):
        if url.endswith("/hide"):
            return _FakeResponse(
                {
                    "stegotext": "bad",
                    "payload_bytes": 4,
                    "is_truncated": False,
                }
            )
        return _FakeResponse({"decode_ok": True, "secret": "hello"})

    monkeypatch.setattr(svc.requests, "post", _fake_post)
    result = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two.", "Sentence three."],
            max_retries=2,
        )
    )
    assert result["accepted"] is False
    assert "payload_size_mismatch" in str(result["reason"])
    assert result["attempt"] == 2


def test_run_comparison_returns_partial_on_truncated_hide(monkeypatch) -> None:
    def _fake_post(url: str, json: dict, timeout: int):
        assert url.endswith("/hide")
        return _FakeResponse(
            {
                "stegotext": "partial stego text",
                "payload_bytes": 5,
                "is_truncated": True,
                "used_bits": 32,
                "target_bits": 56,
                "ppl": 10.0,
                "params_used": {"mode": "huffman"},
            }
        )

    monkeypatch.setattr(svc.requests, "post", _fake_post)
    result = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two.", "Sentence three."],
            max_retries=2,
        )
    )

    assert result["accepted"] is True
    assert result["partial"] is True
    assert result["reason"] == "partial_payload"
    assert result["payload_bytes_actual"] == 2
    assert result["remaining_payload"] == "llo"
    assert result["encoded_bits"] == 32
    assert result["target_bits"] == 56
    assert result["remaining_bits"] == 24
    assert result["decode_ok"] is None


def test_run_comparison_recovers_partial_from_422_best_candidate(monkeypatch) -> None:
    def _fake_post_json(url: str, payload: dict) -> dict:
        assert url.endswith("/hide")
        body = {
            "detail": {
                "reason": "quality_gate_failed",
                "last_fail_reason": "truncated",
                "best_candidate": {
                    "stegotext": "partial stego text",
                    "used_bits": 40,
                    "target_bits": 56,
                    "payload_bytes": 5,
                    "is_truncated": True,
                    "ppl": 9.0,
                    "params_used": {"mode": "huffman"},
                },
            }
        }
        raise RuntimeError(
            svc.json.dumps(
                {
                    "kind": "http_error",
                    "status_code": 422,
                    "url": url,
                    "response_body": svc.json.dumps(body),
                }
            )
        )

    monkeypatch.setattr(svc, "post_json", _fake_post_json)
    result = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two.", "Sentence three."],
            max_retries=1,
        )
    )

    assert result["accepted"] is True
    assert result["partial"] is True
    assert result["payload_bytes_actual"] == 3
    assert result["remaining_payload"] == "lo"
    assert result["remaining_bits"] == 16


def test_dynamic_frames_aggregate_verified_payload(monkeypatch) -> None:
    def _fake_single(sample: svc.ComparisonInput) -> dict:
        return {
            "accepted": True,
            "payload_bytes_actual": len(sample.target_payload.encode("utf-8")),
            "encoded_bits": 72,
            "stegotext": "A concise verified carrier.",
            "decode_ok": True,
        }

    monkeypatch.setattr(svc, "run_comparison_sample", _fake_single)
    result = svc.run_comparison_frames(
        svc.ComparisonInput(
            target_payload="12345678",
            server_url="http://127.0.0.1:9000",
            cover_texts=["One real comment.", "Another real comment."],
        )
    )

    assert result["accepted"] is True
    assert result["carrier_count"] == 1
    assert result["payload_bits_encoded"] == 64
    assert result["protocol_overhead_bits"] == 8


def test_dynamic_frames_reject_unverified_partial_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "run_comparison_sample",
        lambda sample: {
            "accepted": True,
            "partial": True,
            "payload_bytes_actual": 0,
            "encoded_bits": 16,
            "stegotext": "partial",
            "decode_ok": None,
        },
    )

    result = svc.run_comparison_frames(
        svc.ComparisonInput(
            target_payload="12345678",
            server_url="http://127.0.0.1:9000",
            cover_texts=["One real comment.", "Another real comment."],
        )
    )

    assert result["accepted"] is False
    assert result["payload_bits_encoded"] == 0
    assert result["remaining_payload"] == "12345678"


def test_dynamic_frames_count_carrier_that_exceeds_word_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "run_comparison_sample",
        lambda sample: {
            "accepted": True,
            "payload_bytes_actual": len(sample.target_payload.encode("utf-8")),
            "encoded_bits": 72,
            "stegotext": "Four word generated carrier.",
            "decode_ok": True,
        },
    )

    result = svc.run_comparison_frames(
        svc.ComparisonInput(
            target_payload="12345678",
            server_url="http://127.0.0.1:9000",
            cover_texts=["One real comment.", "Another real comment."],
        ),
        max_total_words=1,
    )

    assert result["accepted"] is False
    assert result["word_count"] == 4


def test_append_jsonl_writes_record(tmp_path: Path) -> None:
    out = tmp_path / "logs" / "x.jsonl"
    svc.append_jsonl(out, {"accepted": True})
    content = out.read_text(encoding="utf-8")
    assert '"accepted": true' in content.lower()
