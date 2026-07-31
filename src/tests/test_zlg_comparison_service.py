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


def test_build_api_prompt_does_not_prime_quotes_or_markdown() -> None:
    """The two habits that turned into gate rejections, not style preferences.

    Bulleted examples primed markdown output, and a trailing ``Comment:`` label
    invited a quoted answer whose closing quote ``complete_sent`` then truncated
    away -- 39 of 133 scale300 gate rejections were unbalanced quotes.
    """
    prompt = svc.build_api_prompt("Reddit news discussion", ["First real one.", "Second one."])
    assert "\n- " not in prompt
    assert not prompt.rstrip().endswith("Comment:")
    assert "quotation marks" in prompt
    assert "First real one." in prompt
    assert "Second one." in prompt


def test_run_comparison_success_with_reveal(monkeypatch) -> None:
    calls: list[dict] = []

    def _fake_post(url: str, json: dict, timeout: int):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/hide"):
            return _FakeResponse(
                {
                    "stegotext": "stego text",
                    "payload_bytes": 5,
                    "payload_bits": 40,
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
    reveal_body = calls[1]["json"]
    # Per docs/stego_api_agent_guide.md: without payload_bits_len the server falls back to a
    # legacy 16-bit-header framed decode, which misreads headerless stegotext.
    assert reveal_body["payload_bits_len"] == 40


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


def _http_error(status_code: int, reason: str | None) -> RuntimeError:
    detail: dict = {"reason": reason} if reason else {}
    return RuntimeError(
        svc.json.dumps(
            {
                "kind": "http_error",
                "status_code": status_code,
                "url": "http://127.0.0.1:9000/hide",
                "response_body": svc.json.dumps({"detail": detail}) if detail else "",
            }
        )
    )


def test_quality_gate_rejection_is_retried_with_a_fresh_prompt(monkeypatch) -> None:
    """A 422 quality-gate reject is a property of the sampled text, so re-roll."""
    seen_prompts: list[str] = []

    def _fake_post_json(url: str, payload: dict) -> dict:
        if url.endswith("/hide"):
            seen_prompts.append(payload["prompt"])
            if len(seen_prompts) < 3:
                raise _http_error(422, "quality_gate_failed")
            return {
                "stegotext": "clean stego text",
                "payload_bytes": 5,
                "payload_bits": 40,
                "is_truncated": False,
                "ppl": 11.0,
                "params_used": {"mode": "huffman"},
            }
        return {"decode_ok": True, "secret": "hello"}

    monkeypatch.setattr(svc, "post_json", _fake_post_json)
    result = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=[f"Sentence {i}." for i in range(12)],
            max_retries=3,
        )
    )

    assert result["accepted"] is True
    assert result["attempt"] == 3
    assert len(seen_prompts) == 3
    # Retries must resample cover sentences, not resend an identical request.
    assert len(set(seen_prompts)) == 3


def test_non_retryable_hide_error_fails_immediately(monkeypatch) -> None:
    attempts: list[int] = []

    def _fake_post_json(url: str, payload: dict) -> dict:
        attempts.append(1)
        raise _http_error(400, "malformed_request")

    monkeypatch.setattr(svc, "post_json", _fake_post_json)
    result = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two.", "Sentence three."],
            max_retries=4,
        )
    )

    assert result["accepted"] is False
    assert len(attempts) == 1


def test_retry_classifier_distinguishes_transient_from_contract_errors() -> None:
    assert svc.is_retryable_hide_error(_http_error(422, "quality_gate_failed")) is True
    assert svc.is_retryable_hide_error(_http_error(503, None)) is True
    assert svc.is_retryable_hide_error(_http_error(400, "malformed_request")) is False
    assert svc.is_retryable_hide_error(_http_error(422, "bad_field")) is False
    # A non-HTTP failure (connection reset, timeout) carries no status code.
    assert svc.is_retryable_hide_error(RuntimeError("connection reset")) is True


def test_first_attempt_prompt_is_unchanged_by_reseeding() -> None:
    """Attempt 1 must reproduce the pre-change prompt so past runs stay comparable."""
    sample = svc.ComparisonInput(
        target_payload="hello",
        server_url="http://127.0.0.1:9000",
        cover_texts=[f"Sentence {i}." for i in range(12)],
        seed=1234,
    )
    assert svc._prompt_for_attempt(sample, sample.seed) == svc.build_api_prompt(
        corpus=sample.corpus, cover_texts=sample.cover_texts, seed=1234, n_cover=sample.n_cover
    )


def test_prompt_leakage_failure_preserves_the_rejected_text(monkeypatch) -> None:
    def _fake_post_json(url: str, payload: dict) -> dict:
        return {
            "stegotext": "leaked <OUTPUT> marker",
            "payload_bytes": 5,
            "payload_bits": 40,
            "is_truncated": False,
        }

    monkeypatch.setattr(svc, "post_json", _fake_post_json)
    result = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two.", "Sentence three."],
            max_retries=2,
        )
    )

    assert result["accepted"] is False
    assert result["reason"] == "prompt_leakage_detected"
    assert result["rejected_stegotext"] == "leaked <OUTPUT> marker"


def test_failure_stage_separates_harness_faults_from_baseline_faults() -> None:
    """The distinction the scale300 acceptance rate got wrong.

    A harness extraction failure sent no request at all, so it must not be
    reachable from the same bucket as a gate rejection or a decode failure.
    """
    assert svc.classify_failure_stage(None, accepted=True) == "none"
    assert (
        svc.classify_failure_stage("sample_extract_failed: not enough cover sentences")
        == "harness_extract"
    )
    assert svc.classify_failure_stage(str(_http_error(422, "quality_gate_failed"))) == "unknown"
    assert (
        svc.classify_failure_stage(f"hide_request_failed: {_http_error(422, 'quality_gate_failed')}")
        == "quality_gate"
    )
    assert (
        svc.classify_failure_stage(f"hide_request_failed: {_http_error(503, None)}")
        == "hide_request"
    )
    assert svc.classify_failure_stage("prompt_leakage_detected") == "leakage_check"
    assert svc.classify_failure_stage("reveal_payload_mismatch") == "reveal"
    assert svc.classify_failure_stage("hide_truncated") == "capacity"
    assert svc.classify_failure_stage("payload_size_mismatch: expected=5, got=2") == "capacity"
    assert svc.classify_failure_stage("something we have never seen") == "unknown"


def test_every_comparison_result_carries_a_failure_stage(monkeypatch) -> None:
    def _fake_post_json(url: str, payload: dict) -> dict:
        return {
            "stegotext": "A perfectly ordinary comment about the weather.",
            "payload_bytes": 5,
            "payload_bits": 40,
            "is_truncated": False,
            "decode_ok": True,
            "secret": "hello",
        }

    monkeypatch.setattr(svc, "post_json", _fake_post_json)
    accepted = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two."],
        )
    )
    assert accepted["accepted"] is True
    assert accepted["failure_stage"] == "none"

    monkeypatch.setattr(
        svc, "post_json", lambda url, payload: (_ for _ in ()).throw(_http_error(400, "bad_field"))
    )
    failed = svc.run_comparison_sample(
        svc.ComparisonInput(
            target_payload="hello",
            server_url="http://127.0.0.1:9000",
            cover_texts=["Sentence one.", "Sentence two."],
        )
    )
    assert failed["accepted"] is False
    assert failed["failure_stage"] == "hide_request"


def test_append_jsonl_writes_record(tmp_path: Path) -> None:
    out = tmp_path / "logs" / "x.jsonl"
    svc.append_jsonl(out, {"accepted": True})
    content = out.read_text(encoding="utf-8")
    assert '"accepted": true' in content.lower()
