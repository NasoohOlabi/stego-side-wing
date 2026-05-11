"""Steganographic encoding pipeline with n8n parity logic."""

import json
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from infrastructure.config import (
    get_workflow_encoding_secret,
    get_workflow_encoding_settings,
    get_workflow_payload_transform,
    get_workflow_stego_default_max_retries,
    get_workflow_stego_generation_mode,
    get_workflow_stego_llm_temperature,
    get_workflow_stego_prompt_style,
    get_workflow_stego_sample_angle_count,
    resolve_workflow_llm_provider_and_model,
)
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.pipelines.decode import DECODE_LLM_MODEL, DecodePipeline
from workflows.utils import stego_codec
from workflows.utils.naturalness_gate import (
    comment_plausibility_gate,
    naturalness_gate_enabled,
)
from workflows.utils.output_results_shape import (
    assert_valid_n8n_stego_artifact,
    n8n_save_object_body,
)
from workflows.utils.stego_codec import (
    augment_post as codec_augment_post,
)
from workflows.utils.stego_codec import (
    augment_post_with_selection_bits as codec_augment_post_with_selection_bits,
)
from workflows.utils.stego_codec import (
    build_dictionary as codec_build_dictionary,
)
from workflows.utils.stego_codec import (
    build_dictionary_report as codec_build_dictionary_report,
)
from workflows.utils.stego_codec import (
    compress_payload as codec_compress_payload,
)
from workflows.utils.stego_codec import (
    protect_payload,
)
from workflows.utils.stego_codec import (
    embed_in_angle_selection as codec_embed_in_angle_selection,
)
from workflows.utils.stego_codec import (
    embed_in_comment_selection as codec_embed_in_comment_selection,
)
from workflows.utils.stego_codec import (
    flatten_comments,
)
from workflows.utils.protocol_utils import stable_hash
from workflows.utils.workflow_llm_prompts import stego_encode_prompts_for_style

# Backward-compatible names for tests and callers.
MAX_LITERAL_LEN = stego_codec.MAX_LITERAL_LEN
_is_non_empty_string = stego_codec.is_non_empty_string
_flatten_comments = flatten_comments
_get_bit_width = stego_codec.get_bit_width
_take_bits = stego_codec.take_bits

STEGO_WORKFLOW_ID = "27rZrYtywu3k9e7Q"
STEGO_DEFAULT_OFFSET = 1
STEGO_LLM_MODEL = DECODE_LLM_MODEL
_STEGO_LOG = logger.bind(component="StegoPipeline")
_STOPWORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "but",
    "by",
    "for",
    "from",
    "get",
    "gets",
    "got",
    "had",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "like",
    "me",
    "more",
    "my",
    "no",
    "not",
    "of",
    "on",
    "or",
    "our",
    "out",
    "she",
    "so",
    "that",
    "the",
    "their",
    "them",
    "there",
    "they",
    "this",
    "to",
    "too",
    "was",
    "we",
    "were",
    "what",
    "when",
    "with",
    "would",
    "you",
    "your",
}
_GENERIC_EDITORIAL_PATTERNS = (
    "the bigger issue",
    "the lesson here",
    "at the end of the day",
    "what keeps sticking with me",
    "zoom out",
    "this is really about",
    "wake up call",
)


def _elapsed_ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _stego_log_bind(
    log_area: str,
    *,
    log_op: str = "encode",
    prompt_role: str | None = None,
    llm_stage: str | None = None,
    process_event: str | None = None,
    timing_phase: str | None = None,
) -> Any:
    """Structured stego log context (log_domain / log_area / log_op); message stays prefix-free."""
    fields: dict[str, Any] = {
        "log_domain": "stego",
        "log_area": log_area,
        "log_op": log_op,
    }
    if prompt_role is not None:
        fields["prompt_role"] = prompt_role
    if llm_stage is not None:
        fields["llm_stage"] = llm_stage
    if process_event is not None:
        fields["process_event"] = process_event
    if timing_phase is not None:
        fields["timing_phase"] = timing_phase
    return _STEGO_LOG.bind(**fields)


# Must match stego encode system template rule 1 (exactly three strings).
STEGO_LLM_JSON_STRING_COUNT = 3


def _stego_clean_json_string_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if isinstance(x, str) and str(x).strip()]


def _stego_comment_strings_from_parsed(parsed: Any) -> list[str] | None:
    """Three non-empty strings: top-level array or dict texts/comments/items/output."""
    direct = _stego_clean_json_string_list(parsed)
    if direct:
        return direct if len(direct) == STEGO_LLM_JSON_STRING_COUNT else None
    if isinstance(parsed, dict):
        for key in ("texts", "comments", "items", "output"):
            clean = _stego_clean_json_string_list(parsed.get(key))
            if clean and len(clean) == STEGO_LLM_JSON_STRING_COUNT:
                return clean
    return None


def _eq_angle(lhs: dict[str, Any] | None, rhs: dict[str, Any] | None) -> bool:
    if lhs is None and rhs is None:
        return True
    if lhs is None or rhs is None:
        return False
    return (
        lhs.get("category") == rhs.get("category")
        and lhs.get("tangent") == rhs.get("tangent")
        and lhs.get("source_quote") == rhs.get("source_quote")
    )


def _angle_summary(angle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(angle, dict):
        return None
    return {
        "idx": angle.get("idx"),
        "category": angle.get("category"),
        "tangent": angle.get("tangent"),
        "source_quote": angle.get("source_quote"),
        "source_document": angle.get("source_document"),
    }


def _anchor_comment_body(post_augmentation: dict[str, Any] | None) -> str:
    if not isinstance(post_augmentation, dict):
        return ""
    chain = post_augmentation.get("commentEmbedding", {}).get("pickedCommentChain", [])
    if not isinstance(chain, list):
        return ""
    for comment in reversed(chain):
        if isinstance(comment, dict) and isinstance(comment.get("body"), str):
            body = " ".join(comment["body"].split())
            if body:
                return body
    return ""


def _text_preview(text: Any, max_len: int = 180) -> str:
    if not isinstance(text, str):
        return ""
    stripped = " ".join(text.split())
    return stripped if len(stripped) <= max_len else f"{stripped[:max_len]}..."


def _prompt_style_for_attempt(configured_style: str, retry_count: int) -> str:
    if configured_style == "natural_sharpened":
        return "natural"
    if configured_style == "natural_then_anchor_retry":
        return "guided_natural" if retry_count == 0 else "anchored"
    return configured_style


def _tokenize_content_words(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9'-]+", text.lower())
        if len(token) >= 4 and token not in _STOPWORDS
    ]


def _context_support_texts(
    post_augmentation: dict[str, Any] | None,
    sample: dict[str, Any] | None,
    selected_angle: dict[str, Any],
) -> list[str]:
    texts: list[str] = []
    if isinstance(sample, dict):
        for key in ("best_match", "source_quote", "tangent", "category"):
            value = sample.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
    for key in ("source_quote", "tangent", "category"):
        value = selected_angle.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)
    if isinstance(post_augmentation, dict):
        comment_embedding = post_augmentation.get("commentEmbedding", {})
        context = comment_embedding.get("context", {})
        for key in ("title", "selftext"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
        picked_chain = comment_embedding.get("pickedCommentChain", [])
        if isinstance(picked_chain, list):
            for comment in picked_chain:
                if not isinstance(comment, dict):
                    continue
                body = comment.get("body")
                if isinstance(body, str) and body.strip():
                    texts.append(body)
    return texts


def _contextuality_gate(
    text: str,
    *,
    post_augmentation: dict[str, Any] | None,
    sample: dict[str, Any] | None,
    selected_angle: dict[str, Any],
) -> dict[str, Any]:
    normalized = " ".join(text.split()).lower()
    support_vocab = set(
        _tokenize_content_words(" ".join(_context_support_texts(post_augmentation, sample, selected_angle)))
    )
    candidate_tokens = _tokenize_content_words(text)
    overlap = [token for token in candidate_tokens if token in support_vocab]
    unsupported = [token for token in candidate_tokens if token not in support_vocab]
    generic_patterns = [pattern for pattern in _GENERIC_EDITORIAL_PATTERNS if pattern in normalized]
    has_supported_phrase = False
    for value in (
        selected_angle.get("source_quote"),
        selected_angle.get("tangent"),
        sample.get("best_match") if isinstance(sample, dict) else None,
    ):
        if isinstance(value, str):
            phrase_tokens = _tokenize_content_words(value)
            if phrase_tokens and any(token in set(overlap) for token in phrase_tokens):
                has_supported_phrase = True
                break
    reasons: list[str] = []
    if generic_patterns:
        reasons.append("generic_editorial_tone")
    if not overlap:
        reasons.append("no_context_overlap")
    if len(unsupported) >= 5 and len(overlap) < 2:
        reasons.append("unsupported_topic_drift")
    if not has_supported_phrase and len(overlap) < 2:
        reasons.append("weak_selected_angle_grounding")
    plausibility = {"enabled": False, "passes": True, "reasons": []}
    if naturalness_gate_enabled():
        plausibility = {"enabled": True, **comment_plausibility_gate(text)}
        plausibility_reasons = plausibility.get("reasons", [])
        if isinstance(plausibility_reasons, list):
            reasons.extend(str(reason) for reason in plausibility_reasons)
    return {
        "passes": not reasons,
        "overlap_tokens": overlap[:12],
        "unsupported_tokens": unsupported[:12],
        "generic_patterns": generic_patterns,
        "plausibility": plausibility,
        "reasons": reasons,
    }


def _clean_angle_anchor_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text:
        return ""
    json_values = re.findall(
        r'"(?:title|summary|source_quote|tangent)"\s*:\s*"([^"]+)"',
        text,
    )
    if json_values:
        titles = re.findall(r'"title"\s*:\s*"([^"]+)"', text)
        text = titles[0] if titles else max(json_values, key=len)
    text = re.sub(r"[\[\]{}]", " ", text)
    text = re.sub(r'"\s*,?\s*"(?:summary|title|error|key_points)"\s*:\s*', " ", text)
    text = " ".join(text.strip(" ,.:;\"'").split())
    if len(text) > 180:
        text = text[:180].rsplit(" ", 1)[0].strip()
    return text


def _selected_angle_anchor_phrase(selected_angle: dict[str, Any]) -> str:
    source_quote = _clean_angle_anchor_text(selected_angle.get("source_quote"))
    if len(_tokenize_content_words(source_quote)) >= 2:
        return source_quote
    tangent = _clean_angle_anchor_text(selected_angle.get("tangent"))
    if len(_tokenize_content_words(tangent)) >= 2:
        return tangent
    return ""


def _with_selected_angle_anchor_variants(
    texts: list[str],
    selected_angle: dict[str, Any],
) -> list[str]:
    phrase = _selected_angle_anchor_phrase(selected_angle)
    if not phrase:
        return texts
    phrase_tokens = set(_tokenize_content_words(phrase))
    if not phrase_tokens:
        return texts
    for text in texts:
        if len(phrase_tokens & set(_tokenize_content_words(text))) >= min(3, len(phrase_tokens)):
            return texts
    if len(phrase.split()) <= 8:
        anchored = f"I can see why people keep coming back to {phrase}."
    else:
        anchored = f"{phrase}. I can see why people keep coming back to that point."
    anchored = " ".join(anchored.split())
    if len(anchored) > 260:
        anchored = anchored[:260].rsplit(" ", 1)[0].strip()
    if anchored and anchored not in texts:
        return [*texts, anchored]
    return texts


def _extractive_candidate_texts(post: dict[str, Any]) -> list[str]:
    bodies = []
    for comment in flatten_comments(post.get("comments", [])):
        body = comment.get("body")
        if isinstance(body, str) and body.strip():
            bodies.append(body.strip())
    selftext = post.get("selftext", "")
    if isinstance(selftext, str) and selftext.strip():
        bodies.append(selftext.strip())
    return bodies


def _extractive_stego_text(post: dict[str, Any], selected_angle: dict[str, Any]) -> str:
    candidates = _extractive_candidate_texts(post)
    for candidate in candidates:
        if _extractive_angle_matches(candidate, selected_angle):
            return "\n".join(candidates)
    return ""


def _extractive_angle_matches(candidate_text: str, selected_angle: dict[str, Any]) -> bool:
    for key in ("source_quote", "tangent"):
        value = selected_angle.get(key)
        if isinstance(value, str) and value.strip() and value.strip() in candidate_text:
            return True
    return False


def _sender_audit_from_post(
    post: dict[str, Any], post_augmentation: dict[str, Any]
) -> dict[str, Any]:
    dictionary_report = codec_build_dictionary_report(post)
    tangents_db = list(post_augmentation.get("angleEmbedding", {}).get("TangentsDB", []))
    search_results = list(post.get("search_results", []) or [])
    selected_urls_hashes = [stable_hash(item) for item in search_results]
    compression = dict(post_augmentation.get("compression", {}))
    return {
        "dictionary_id": dictionary_report["dictionary_id"],
        "dictionary_hash": dictionary_report["texts_hash"],
        "dictionary_count": dictionary_report["entry_count"],
        "angles_hash": stable_hash(tangents_db),
        "angles_count": len(tangents_db),
        "selected_url_hashes": selected_urls_hashes,
        "selected_urls_hash": stable_hash(selected_urls_hashes),
        "selected_angle_index": post_augmentation.get("angleEmbedding", {})
        .get("selectedAngle", {})
        .get("idx"),
        "compression": {
            "method": compression.get("method"),
            "compressed": compression.get("compressed"),
            "compressed_length": compression.get("compressedLength"),
            "original_length": compression.get("originalLength"),
            "compressed_hash": stable_hash(compression.get("compressed", "")),
        },
        "selection_signature": post_augmentation.get("selectionSignature"),
        "comment_bits": post_augmentation.get("commentBits"),
        "angle_bits": post_augmentation.get("angleBits"),
    }


def _log_encode_timing_complete(
    *,
    encode_run_id: str,
    post_id: Any,
    augment_ms: int,
    build_samples_ms: int,
    encode_total_ms: int,
    succeeded: bool,
    retry_count: int,
    timing_outcome: str,
) -> None:
    _stego_log_bind("timing", timing_phase="encode_complete").bind(
        stego_encode_run_id=encode_run_id,
        augment_ms=augment_ms,
        build_samples_ms=build_samples_ms,
        encode_total_ms=encode_total_ms,
        succeeded=succeeded,
        retry_count=retry_count,
        timing_outcome=timing_outcome,
    ).info(
        "post_id={} encode_total_ms={} augment_ms={} build_samples_ms={} succeeded={} retry_count={} outcome={}",
        post_id,
        encode_total_ms,
        augment_ms,
        build_samples_ms,
        succeeded,
        retry_count,
        timing_outcome,
    )


class StegoPipeline:
    """Owns LLM and backend adapters; runs encode/process_post for stego artifacts.

    Logs use module ``_STEGO_LOG`` so instances created via ``__new__`` (tests) still emit
    with component ``StegoPipeline`` without running ``__init__``.
    """

    def __init__(self) -> None:
        self.backend = BackendAPIAdapter()
        self.llm = LLMAdapter()
        self.decode_pipeline = DecodePipeline()
        self.config = get_config()

    def _load_default_payload_and_tag(self) -> tuple[str | None, str | None]:
        """Load default payload/tag from the n8n Stego workflow SetSecretData node."""
        workflow_path = (
            Path(__file__).resolve().parents[3] / "workflows" / f"{STEGO_WORKFLOW_ID}.json"
        )
        if not workflow_path.exists():
            return None, None

        try:
            with workflow_path.open("r", encoding="utf-8") as workflow_file:
                workflow = json.load(workflow_file)
        except Exception:
            return None, None

        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return None, None

        payload_value: str | None = None
        for node in nodes:
            if not isinstance(node, dict) or node.get("name") != "SetSecretData":
                continue
            assignments = node.get("parameters", {}).get("assignments", {}).get("assignments", [])
            if not isinstance(assignments, list):
                continue
            for assignment in assignments:
                if (
                    isinstance(assignment, dict)
                    and assignment.get("name") == "payload"
                    and isinstance(assignment.get("value"), str)
                ):
                    payload_value = assignment["value"]
                    break
            if payload_value is not None:
                break

        if not payload_value:
            return None, None

        payload_candidate = payload_value.strip()
        if payload_candidate.startswith("="):
            payload_candidate = payload_candidate[1:].strip()

        try:
            parsed = json.loads(payload_candidate)
        except json.JSONDecodeError:
            return payload_candidate, None

        if isinstance(parsed, dict):
            parsed_payload = parsed.get("payload")
            parsed_tag = parsed.get("tag")
            payload = parsed_payload if isinstance(parsed_payload, str) else None
            tag = parsed_tag if isinstance(parsed_tag, str) else None
            return payload, tag

        if isinstance(parsed, str):
            return parsed, None
        return None, None

    def _build_dictionary(self, post: dict[str, Any]) -> list[str]:
        return codec_build_dictionary(post)

    def _compress_payload(self, payload: str, dictionary: list[str]) -> dict[str, Any]:
        return codec_compress_payload(payload, dictionary)

    def _embed_in_comment_selection(self, bits: str, post: dict[str, Any]) -> dict[str, Any]:
        return codec_embed_in_comment_selection(bits, post)

    def _embed_in_angle_selection(
        self, bits: str, nested_angles: list[list[dict[str, Any]]]
    ) -> dict[str, Any]:
        return codec_embed_in_angle_selection(bits, nested_angles)

    def _augment_post(self, payload: str, post: dict[str, Any]) -> dict[str, Any]:
        return codec_augment_post(payload, post)

    def _build_samples(
        self, post_augmentation: dict[str, Any], post: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        angle_embedding = post_augmentation.get("angleEmbedding", {})
        sample_count = get_workflow_stego_sample_angle_count()
        candidate_angles = angle_embedding.get("totalAnglesSelectedFirst", [])[:sample_count]
        needles = [str(a.get("source_quote", "")) for a in candidate_angles if isinstance(a, dict)]
        haystack = post.get("search_results", [])
        if not isinstance(haystack, list):
            haystack = []

        source_response = self.backend.needle_finder_batch(needles=needles, haystack=haystack)
        source_results = source_response.get("results", [])

        samples: list[dict[str, Any]] = []
        for idx, angle in enumerate(candidate_angles):
            if not isinstance(angle, dict):
                continue
            match_data = source_results[idx] if idx < len(source_results) else {}
            best_match = match_data.get("best_match", "") if isinstance(match_data, dict) else ""
            sample = dict(angle)
            sample["best_match"] = best_match
            samples.append(sample)

        tangents_db = angle_embedding.get("TangentsDB", [])
        if not isinstance(tangents_db, list):
            tangents_db = []
        return samples, tangents_db

    def _encode_extractive_zero_kld(
        self,
        *,
        payload: str,
        embedded_payload: str,
        post: dict[str, Any],
        tag: str | None,
        sender_audit: dict[str, Any],
        post_augmentation: dict[str, Any],
        selected_angle: dict[str, Any],
        selected_idx: Any,
    ) -> dict[str, Any] | None:
        generation_mode = get_workflow_stego_generation_mode()
        if generation_mode not in {"extractive_zero_kld", "hybrid_extract"}:
            return None
        visible_text = _extractive_stego_text(post, selected_angle)
        if not _is_non_empty_string(visible_text):
            return None
        if not _extractive_angle_matches(visible_text, selected_angle):
            return None
        stego_text = visible_text
        sender_audit["payload_carrier"] = "selection_channel"
        sender_audit["payload_bytes"] = len(payload.encode("utf-8"))
        sender_audit["embedded_payload_bytes"] = len(embedded_payload.encode("utf-8"))
        return {
            "stego_text": stego_text,
            "post": post,
            "selected_angle": selected_angle,
            "angle_index": selected_idx,
            "succeeded": True,
            "retry_count": 0,
            "tag": tag,
            "sender_audit": sender_audit,
            "breakdown": {
                "mode": "extractive_zero_kld",
                "payload_carrier": "selection_channel",
                "visible_text_len": len(visible_text),
                "embedded_payload_bytes": len(embedded_payload.encode("utf-8")),
                "embedded_payload_bits": len(embedded_payload.encode("utf-8")) * 8,
                "raw_payload_bytes": len(payload.encode("utf-8")),
            },
            "embedding": post_augmentation,
        }

    def _build_prompt(
        self,
        sample: dict[str, Any],
        comment_embedding: dict[str, Any],
        *,
        prompt_style: str,
    ) -> tuple[str, str]:
        context = comment_embedding.get("context", {})
        title = context.get("title", "")
        author = context.get("author", "")
        selftext = context.get("selftext", "")
        title = title if isinstance(title, str) else ""
        author = author if isinstance(author, str) else ""
        selftext = selftext if isinstance(selftext, str) else ""

        picked_chain = comment_embedding.get("pickedCommentChain", [])
        chain_section = ""
        if isinstance(picked_chain, list) and picked_chain:
            rendered: list[str] = []
            for comment in picked_chain:
                if not isinstance(comment, dict):
                    continue
                raw_name = comment.get("name")
                raw_body = comment.get("body")
                body = raw_body.strip() if isinstance(raw_body, str) else ""
                if not body:
                    continue
                name = raw_name.strip() if isinstance(raw_name, str) else ""
                if not name:
                    name = "Unknown"
                label = "commented" if not rendered else "replied"
                rendered.append(f"{name} {label}:\n{body}")
            if rendered:
                chain_section = "\n---\n" + "\n---\n".join(rendered)

        enc = stego_encode_prompts_for_style(prompt_style)
        prompt = enc.user_template.format(
            best_match=str(sample.get("best_match", "")),
            target_category=str(sample.get("category", "")),
            target_tangent=str(sample.get("tangent", "")),
            target_source_quote=str(sample.get("source_quote", "")),
            title=title,
            author=author,
            selftext=selftext,
            chain_section=chain_section,
        )
        system_message = enc.system_template.format(
            tangent=str(sample.get("tangent", "")),
            category=str(sample.get("category", "")),
            source_quote=str(sample.get("source_quote", "")),
        )
        return prompt, system_message

    def _generate_stego_texts(
        self,
        sample: dict[str, Any],
        comment_embedding: dict[str, Any],
        *,
        prompt_style: str,
        sample_index: int = 0,
        encode_run_id: str = "",
        llm_timings: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        def _extract_json_block(raw: str) -> str:
            stripped = raw.strip()
            if not stripped.startswith("```"):
                return stripped
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                return "\n".join(lines[1:-1]).strip()
            return stripped

        prompt, system_message = self._build_prompt(
            sample,
            comment_embedding,
            prompt_style=prompt_style,
        )
        _stego_log_bind("prompt").info(
            "category={} tangent={} source_quote={}",
            sample.get("category"),
            _text_preview(sample.get("tangent", ""), max_len=120),
            _text_preview(sample.get("source_quote", ""), max_len=120),
        )
        _stego_log_bind("prompt", prompt_role="system").info("{}", system_message)
        _stego_log_bind("prompt", prompt_role="user").info("{}", prompt)
        t_llm = time.perf_counter()
        provider, model = resolve_workflow_llm_provider_and_model(STEGO_LLM_MODEL)
        response = self.llm.call_llm(
            prompt=prompt,
            system_message=system_message,
            model=model,
            provider=provider,
            temperature=get_workflow_stego_llm_temperature(),
        )
        llm_wall_ms = _elapsed_ms_since(t_llm)
        meta = self.llm.last_call_metadata or {}
        llm_adapter_ms = meta.get("elapsed_ms")
        timing_record = {
            "sample_index": sample_index,
            "prompt_style": prompt_style,
            "provider": provider,
            "model": model,
            "llm_wall_ms": llm_wall_ms,
            "llm_adapter_reported_ms": llm_adapter_ms,
        }
        text = response.strip()
        _stego_log_bind("llm", llm_stage="raw").info("{}", text)

        # Accept plain JSON and markdown-fenced JSON payloads.
        json_candidates = [text, _extract_json_block(text)]
        for payload in json_candidates:
            if not payload:
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            strings = _stego_comment_strings_from_parsed(parsed)
            if strings:
                _stego_log_bind("llm", llm_stage="parsed").info(
                    "extracted={} mode=json_contract",
                    len(strings),
                )
                tb = _stego_log_bind("timing", timing_phase="encode_llm_sample").bind(
                    stego_encode_run_id=encode_run_id,
                    sample_index=sample_index,
                    llm_wall_ms=llm_wall_ms,
                    llm_adapter_reported_ms=llm_adapter_ms,
                )
                tb.info(
                    "category={} ok={} llm_wall_ms={} llm_adapter_reported_ms={}",
                    sample.get("category"),
                    True,
                    llm_wall_ms,
                    llm_adapter_ms,
                )
                if llm_timings is not None:
                    llm_timings.append(dict(timing_record, ok=True))
                return strings

        tb = _stego_log_bind("timing", timing_phase="encode_llm_sample").bind(
            stego_encode_run_id=encode_run_id,
            sample_index=sample_index,
            llm_wall_ms=llm_wall_ms,
            llm_adapter_reported_ms=llm_adapter_ms,
        )
        tb.warning(
            "category={} ok={} llm_wall_ms={} llm_adapter_reported_ms={}",
            sample.get("category"),
            False,
            llm_wall_ms,
            llm_adapter_ms,
        )
        if llm_timings is not None:
            llm_timings.append(dict(timing_record, ok=False))
        _stego_log_bind("llm", llm_stage="parse").error(
            "Strict JSON contract failed tangent={} preview={}",
            _text_preview(sample.get("tangent", ""), max_len=120),
            _text_preview(text, max_len=200),
        )
        raise RuntimeError(
            "Stego LLM output must be valid JSON: exactly "
            f"{STEGO_LLM_JSON_STRING_COUNT} non-empty strings (array or "
            "object with texts/comments/items/output), optionally in a "
            "markdown code fence — no prose before/after."
        )

    def _decode_candidate(
        self,
        *,
        text: str,
        few_shots: list[dict[str, Any]],
        tangents_db: list[dict[str, Any]],
        strict_mode: bool,
    ) -> tuple[int | None, int]:
        t_dec = time.perf_counter()
        decoded_idx = self.decode_pipeline.decode(
            stego_text=text,
            angles=tangents_db,
            few_shots=few_shots,
            strict_mode=strict_mode,
        )
        return decoded_idx, _elapsed_ms_since(t_dec)

    def _candidate_distance_bucket(
        self,
        decoded_idx: int | None,
        selected_idx: Any,
    ) -> str:
        if not isinstance(decoded_idx, int) or not isinstance(selected_idx, int):
            return "unknown"
        distance = abs(decoded_idx - selected_idx)
        if distance == 0:
            return "exact"
        if distance <= 2:
            return "adjacent"
        return "far"

    def _evaluate_candidate_groups(
        self,
        *,
        encoded_results: list[dict[str, Any]],
        tangents_db: list[dict[str, Any]],
        selected_angle: dict[str, Any],
        post_augmentation: dict[str, Any],
        encode_run_id: str = "",
    ) -> dict[str, Any]:
        t_eval = time.perf_counter()
        selected_idx = selected_angle.get("idx")
        evaluations: list[dict[str, Any]] = []
        for group_idx, group in enumerate(encoded_results):
            texts = group.get("texts", [])
            if not isinstance(texts, list):
                continue
            few_shots = [item for idx, item in enumerate(encoded_results) if idx != group_idx]
            for candidate_idx, text in enumerate(texts):
                if not _is_non_empty_string(text):
                    continue
                strict_decoded_idx, strict_ms = self._decode_candidate(
                    text=text,
                    few_shots=few_shots,
                    tangents_db=tangents_db,
                    strict_mode=True,
                )
                relaxed_decoded_idx = strict_decoded_idx
                relaxed_ms = strict_ms
                if strict_decoded_idx is None:
                    relaxed_decoded_idx, relaxed_ms = self._decode_candidate(
                        text=text,
                        few_shots=few_shots,
                        tangents_db=tangents_db,
                        strict_mode=False,
                    )
                decoded_obj = None
                if (
                    isinstance(relaxed_decoded_idx, int)
                    and 0 <= relaxed_decoded_idx < len(tangents_db)
                ):
                    decoded_obj = tangents_db[relaxed_decoded_idx]
                context_gate = _contextuality_gate(
                    text,
                    post_augmentation=post_augmentation,
                    sample=group,
                    selected_angle=selected_angle,
                )
                exact_strict_match = strict_decoded_idx == selected_idx
                exact_relaxed_match = relaxed_decoded_idx == selected_idx
                distance_bucket = self._candidate_distance_bucket(relaxed_decoded_idx, selected_idx)
                rejection_reasons = list(context_gate["reasons"])
                if not exact_strict_match:
                    if distance_bucket == "adjacent":
                        rejection_reasons.append("adjacent_angle_mismatch")
                    elif exact_relaxed_match:
                        rejection_reasons.append("weak_decoder_mode")
                    else:
                        rejection_reasons.append("decode_mismatch")
                evaluations.append(
                    {
                        "group_index": group_idx,
                        "candidate_index": candidate_idx,
                        "text": text,
                        "text_preview": _text_preview(text),
                        "prompt_style": group.get("prompt_style"),
                        "sample_category": group.get("category"),
                        "sample_tangent": group.get("tangent"),
                        "strict_decoded_index": strict_decoded_idx,
                        "decoded_index": relaxed_decoded_idx,
                        "decoded_angle": _angle_summary(decoded_obj),
                        "distance_bucket": distance_bucket,
                        "strict_decode_ms": strict_ms,
                        "relaxed_decode_ms": relaxed_ms,
                        "context_gate": context_gate,
                        "matches_selected_angle": exact_relaxed_match,
                        "accepted": exact_strict_match and context_gate["passes"],
                        "rejection_reasons": rejection_reasons,
                    }
                )
        evaluations.sort(
            key=lambda item: (
                0 if item["accepted"] else 1,
                0 if item["context_gate"]["passes"] else 1,
                0
                if item["distance_bucket"] == "exact"
                else (1 if item["distance_bucket"] == "adjacent" else 2),
                len(item["context_gate"]["unsupported_tokens"]),
                item["group_index"],
                item["candidate_index"],
            )
        )
        accepted = next((item for item in evaluations if item["accepted"]), None)
        promising_candidates = [
            item
            for item in evaluations
            if item["distance_bucket"] in {"exact", "adjacent"}
            or item["matches_selected_angle"]
        ][:3]
        promising = promising_candidates[0] if promising_candidates else None
        eval_ms = _elapsed_ms_since(t_eval)
        _stego_log_bind("timing", timing_phase="cross_validate").bind(
            stego_encode_run_id=encode_run_id,
            elapsed_ms=eval_ms,
            candidate_count=len(evaluations),
            succeeded=accepted is not None,
        ).info(
            "elapsed_ms={} candidate_count={} succeeded={}",
            eval_ms,
            len(evaluations),
            accepted is not None,
        )
        return {
            "succeeded": accepted is not None,
            "accepted_candidate": accepted,
            "promising_candidate": promising,
            "promising_candidates": promising_candidates,
            "validationDetails": {
                "selected_angle": _angle_summary(selected_angle),
                "candidates": [
                    {
                        "group_index": item["group_index"],
                        "candidate_index": item["candidate_index"],
                        "decoded_index": item["decoded_index"],
                        "strict_decoded_index": item["strict_decoded_index"],
                        "decoded_angle": item["decoded_angle"],
                        "distance_bucket": item["distance_bucket"],
                        "matches_selected_angle": item["matches_selected_angle"],
                        "accepted": item["accepted"],
                        "rejection_reasons": item["rejection_reasons"],
                        "text_preview": item["text_preview"],
                        "context_gate": item["context_gate"],
                    }
                    for item in evaluations
                ],
            },
        }

    def _revise_candidate_text_contextually(
        self,
        *,
        candidate_text: str,
        sample: dict[str, Any],
        comment_embedding: dict[str, Any],
        encode_run_id: str = "",
        sample_index: int = 0,
        llm_timings: list[dict[str, Any]] | None = None,
    ) -> str:
        context = comment_embedding.get("context", {})
        picked_chain = comment_embedding.get("pickedCommentChain", [])
        chain_lines: list[str] = []
        if isinstance(picked_chain, list):
            for comment in picked_chain[-3:]:
                if not isinstance(comment, dict):
                    continue
                name = comment.get("name") if isinstance(comment.get("name"), str) else "Unknown"
                raw_body = comment.get("body")
                body = raw_body if isinstance(raw_body, str) else ""
                body = " ".join(body.split())
                if body:
                    chain_lines.append(f"{name}: {body}")
        user_prompt = (
            "Revise the draft reply with minimal edits so it stays a plausible Reddit reply to the thread.\n"
            "Rules:\n"
            "1. Keep the same stance, topic, and overall tone.\n"
            "2. Do not add new facts, slogans, or broad policy framing.\n"
            "3. Keep it grounded in the post and comment chain.\n"
            "4. Make the target tangent slightly clearer using only ideas already present in the context.\n"
            "5. Return JSON with exactly one key: {\"text\": \"...\"}.\n\n"
            f"Post title: {context.get('title', '')}\n"
            f"Post body: {context.get('selftext', '')}\n"
            f"Comment chain:\n" + "\n".join(chain_lines) + "\n\n"
            f"Target tangent: {sample.get('tangent', '')}\n"
            f"Target source quote: {sample.get('source_quote', '')}\n"
            f"Best supporting match: {sample.get('best_match', '')}\n"
            f"Draft reply: {candidate_text}"
        )
        system_message = (
            "You are revising one Reddit reply for contextual faithfulness and decodability. "
            "Use only visible ordinary text. Preserve plausibility and avoid generic editorial language."
        )
        provider, model = resolve_workflow_llm_provider_and_model(STEGO_LLM_MODEL)
        t_llm = time.perf_counter()
        response = self.llm.call_llm(
            prompt=user_prompt,
            system_message=system_message,
            model=model,
            provider=provider,
            temperature=get_workflow_stego_llm_temperature(),
        )
        llm_wall_ms = _elapsed_ms_since(t_llm)
        meta = self.llm.last_call_metadata or {}
        llm_adapter_ms = meta.get("elapsed_ms")
        if llm_timings is not None:
            llm_timings.append(
                {
                    "sample_index": sample_index,
                    "prompt_style": "natural_sharpened",
                    "provider": provider,
                    "model": model,
                    "llm_wall_ms": llm_wall_ms,
                    "llm_adapter_reported_ms": llm_adapter_ms,
                    "ok": True,
                }
            )
        parsed = json.loads(response.strip())
        revised = parsed.get("text") if isinstance(parsed, dict) else None
        if not _is_non_empty_string(revised):
            raise RuntimeError("Contextual sharpen pass returned empty text")
        _stego_log_bind("timing", timing_phase="encode_llm_sample").bind(
            stego_encode_run_id=encode_run_id,
            sample_index=sample_index,
            llm_wall_ms=llm_wall_ms,
            llm_adapter_reported_ms=llm_adapter_ms,
        ).info(
            "category={} ok={} llm_wall_ms={} llm_adapter_reported_ms={}",
            sample.get("category"),
            True,
            llm_wall_ms,
            llm_adapter_ms,
        )
        return str(revised).strip()

    def _cross_validate(
        self,
        candidate_texts: list[str],
        few_shots: list[dict[str, Any]],
        tangents_db: list[dict[str, Any]],
        selected_angle: dict[str, Any],
        *,
        encode_run_id: str = "",
    ) -> dict[str, Any]:
        t_cv = time.perf_counter()
        decoded_indices: list[int | None] = []
        decodeds: list[dict[str, Any] | None] = []
        for idx, text in enumerate(candidate_texts):
            t_dec = time.perf_counter()
            decoded_idx = self.decode_pipeline.decode(
                stego_text=text,
                angles=tangents_db,
                few_shots=few_shots,
            )
            dec_ms = _elapsed_ms_since(t_dec)
            db = _stego_log_bind("timing", timing_phase="decode_candidate").bind(
                stego_encode_run_id=encode_run_id,
                candidate_index=idx,
                elapsed_ms=dec_ms,
            )
            db.debug(
                "candidate_index={} elapsed_ms={} decoded_idx={}",
                idx,
                dec_ms,
                decoded_idx,
            )
            decoded_indices.append(decoded_idx)
            if isinstance(decoded_idx, int) and 0 <= decoded_idx < len(tangents_db):
                decoded = tangents_db[decoded_idx]
                decodeds.append(decoded)
            else:
                decodeds.append(None)

        validation_candidates: list[dict[str, Any]] = []
        selected_summary = _angle_summary(selected_angle)
        for idx, text in enumerate(candidate_texts):
            decoded_obj = decodeds[idx] if idx < len(decodeds) else None
            decoded_idx = decoded_indices[idx] if idx < len(decoded_indices) else None
            validation_candidates.append(
                {
                    "candidate_index": idx,
                    "decoded_index": decoded_idx,
                    "decoded_angle": _angle_summary(decoded_obj),
                    "matches_selected_angle": _eq_angle(decoded_obj, selected_angle),
                    "text_preview": _text_preview(text),
                }
            )

        success_idx = -1
        for idx, decoded_obj in enumerate(decodeds):
            candidate_text = candidate_texts[idx] if idx < len(candidate_texts) else None
            if _eq_angle(decoded_obj, selected_angle) and _is_non_empty_string(candidate_text):
                success_idx = idx
                break

        cv_ms = _elapsed_ms_since(t_cv)
        _stego_log_bind("timing", timing_phase="cross_validate").bind(
            stego_encode_run_id=encode_run_id,
            elapsed_ms=cv_ms,
            candidate_count=len(candidate_texts),
            succeeded=success_idx != -1,
        ).info(
            "elapsed_ms={} candidate_count={} succeeded={}",
            cv_ms,
            len(candidate_texts),
            success_idx != -1,
        )

        if success_idx != -1:
            return {
                "succeeded": True,
                "stegoText": candidate_texts[success_idx],
                "successIdx": success_idx,
                "decodedIndices": decoded_indices,
                "validationDetails": {
                    "selected_angle": selected_summary,
                    "candidates": validation_candidates,
                },
            }

        breakdown: dict[str, Any] = {}
        for idx, text in enumerate(candidate_texts):
            breakdown[text] = decodeds[idx]
        return {
            "succeeded": False,
            "breakDown": breakdown,
            "decodedIndices": decoded_indices,
            "validationDetails": {
                "selected_angle": selected_summary,
                "candidates": validation_candidates,
            },
        }

    def encode(
        self,
        payload: str,
        post: dict[str, Any],
        tag: str | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """
        Encode payload into post using steganography.

        This implementation mirrors the n8n Stego workflow:
        1) Post augmentation (compression + comment/angle embedding)
        2) Source matching and sample construction
        3) Candidate generation + decode cross-validation
        4) Retry loop when validation fails
        """
        angles = post.get("angles", [])
        if not isinstance(angles, list) or not angles:
            raise ValueError("Post must have angles")

        resolved_max_retries = (
            get_workflow_stego_default_max_retries() if max_retries is None else max_retries
        )
        payload_transform = get_workflow_payload_transform()
        embedded_payload = protect_payload(
            payload,
            transform=payload_transform,
            secret=get_workflow_encoding_secret(),
        )
        post_id = post.get("id")
        encode_run_id = uuid4().hex
        t_encode = time.perf_counter()
        _stego_log_bind("start").bind(stego_encode_run_id=encode_run_id).info(
            "post_id={} payload_len={} max_retries={} encoding_profile={}",
            post_id,
            len(payload),
            resolved_max_retries,
            get_workflow_encoding_settings().get("encoding_profile"),
        )

        t_aug = time.perf_counter()
        post_augmentation = self._augment_post(embedded_payload, post)
        sender_audit = _sender_audit_from_post(post, post_augmentation)
        sender_audit["encoding"] = get_workflow_encoding_settings()
        sender_audit["payload_transform"] = payload_transform
        sender_audit["payload_carrier"] = "selection_channel"
        sender_audit["raw_payload_bytes"] = len(payload.encode("utf-8"))
        sender_audit["embedded_payload_bytes"] = len(embedded_payload.encode("utf-8"))
        llm_timings: list[dict[str, Any]] = []
        sender_audit["llm_timings"] = llm_timings
        post_augmentation["senderAudit"] = sender_audit
        augment_ms = _elapsed_ms_since(t_aug)
        _stego_log_bind("timing", timing_phase="augment_post").bind(
            stego_encode_run_id=encode_run_id,
            elapsed_ms=augment_ms,
        ).info("post_id={} elapsed_ms={}", post_id, augment_ms)

        selected_angle = post_augmentation["angleEmbedding"].get("selectedAngle", {})
        selected_idx = selected_angle.get("idx")
        extractive_result = self._encode_extractive_zero_kld(
            payload=payload,
            embedded_payload=embedded_payload,
            post=post,
            tag=tag,
            sender_audit=sender_audit,
            post_augmentation=post_augmentation,
            selected_angle=selected_angle,
            selected_idx=selected_idx,
        )
        if extractive_result is not None:
            _log_encode_timing_complete(
                encode_run_id=encode_run_id,
                post_id=post_id,
                augment_ms=augment_ms,
                build_samples_ms=0,
                encode_total_ms=_elapsed_ms_since(t_encode),
                succeeded=True,
                retry_count=0,
                timing_outcome="extractive_zero_kld",
            )
            return extractive_result

        t_samp = time.perf_counter()
        samples, tangents_db = self._build_samples(post_augmentation, post)
        build_samples_ms = _elapsed_ms_since(t_samp)
        _stego_log_bind("timing", timing_phase="build_samples").bind(
            stego_encode_run_id=encode_run_id,
            elapsed_ms=build_samples_ms,
            samples_count=len(samples),
        ).info(
            "post_id={} elapsed_ms={} samples_count={}",
            post_id,
            build_samples_ms,
            len(samples),
        )

        if not samples:
            _stego_log_bind("prep").error(
                "No samples generated from angle embedding for post_id={}",
                post_id,
            )
            _log_encode_timing_complete(
                encode_run_id=encode_run_id,
                post_id=post_id,
                augment_ms=augment_ms,
                build_samples_ms=build_samples_ms,
                encode_total_ms=_elapsed_ms_since(t_encode),
                succeeded=False,
                retry_count=0,
                timing_outcome="no_samples",
            )
            return {
                "stego_text": "",
                "post": post,
                "succeeded": False,
                "retry_count": 0,
                "tag": tag,
                "sender_audit": sender_audit,
                "error": "No samples generated from angle embedding",
                "error_details": {
                    "reason": "Angle embedding produced zero sample prompts for generation.",
                    "selected_angle": _angle_summary(
                        post_augmentation.get("angleEmbedding", {}).get("selectedAngle")
                    ),
                },
                "embedding": post_augmentation,
            }

        retry_count = 0
        last_breakdown: dict[str, Any] = {}
        configured_prompt_style = str(get_workflow_stego_prompt_style())

        while retry_count <= resolved_max_retries:
            try:
                prompt_style = _prompt_style_for_attempt(configured_prompt_style, retry_count)
                t_attempt = time.perf_counter()
                _stego_log_bind("attempt").info(
                    "post_id={} attempt={}/{} selected_idx={} prompt_style={}",
                    post_id,
                    retry_count + 1,
                    resolved_max_retries + 1,
                    selected_idx,
                    prompt_style,
                )
                encoded_results: list[dict[str, Any]] = []
                t_gen = time.perf_counter()
                for sidx, sample in enumerate(samples):
                    texts = self._generate_stego_texts(
                        sample=sample,
                        comment_embedding=post_augmentation["commentEmbedding"],
                        prompt_style=prompt_style,
                        sample_index=sidx,
                        encode_run_id=encode_run_id,
                        llm_timings=llm_timings,
                    )
                    if _eq_angle(sample, selected_angle):
                        texts = _with_selected_angle_anchor_variants(texts, selected_angle)
                    encoded_results.append(
                        {
                            "category": sample.get("category"),
                            "source_quote": sample.get("source_quote"),
                            "tangent": sample.get("tangent"),
                            "prompt_style": prompt_style,
                            "texts": texts,
                        }
                    )
                generate_ms = _elapsed_ms_since(t_gen)

                primary_texts = encoded_results[0].get("texts", []) if encoded_results else []
                if not primary_texts:
                    raise RuntimeError("Encoder did not return candidate texts")

                _stego_log_bind("generate").info(
                    "post_id={} attempt={} primary_candidates={} generated_groups={}",
                    post_id,
                    retry_count + 1,
                    len(primary_texts),
                    len(encoded_results),
                )
                t_val = time.perf_counter()
                validation = self._evaluate_candidate_groups(
                    encoded_results=encoded_results,
                    tangents_db=tangents_db,
                    selected_angle=selected_angle,
                    post_augmentation=post_augmentation,
                    encode_run_id=encode_run_id,
                )
                validate_ms = _elapsed_ms_since(t_val)
                attempt_ms = _elapsed_ms_since(t_attempt)
                _stego_log_bind("timing", timing_phase="encode_attempt").bind(
                    stego_encode_run_id=encode_run_id,
                    attempt_index=retry_count + 1,
                    generate_ms=generate_ms,
                    validate_ms=validate_ms,
                    attempt_total_ms=attempt_ms,
                    samples_count=len(samples),
                ).info(
                    "post_id={} attempt={} generate_ms={} validate_ms={} attempt_total_ms={}",
                    post_id,
                    retry_count + 1,
                    generate_ms,
                    validate_ms,
                    attempt_ms,
                )

                if validation.get("succeeded"):
                    accepted_candidate = validation.get("accepted_candidate") or {}
                    visible_text = accepted_candidate.get("text")
                    if not _is_non_empty_string(visible_text):
                        raise RuntimeError(
                            "Cross-validation reported success with empty stego text."
                        )
                    visible_text = str(visible_text)
                    stego_text = visible_text
                    _stego_log_bind("success").info(
                        "post_id={} attempt={} success_candidate={} decoded_indices={}",
                        post_id,
                        retry_count + 1,
                        {
                            "group_index": accepted_candidate.get("group_index"),
                            "candidate_index": accepted_candidate.get("candidate_index"),
                        },
                        [
                            item.get("decoded_index")
                            for item in validation.get("validationDetails", {}).get("candidates", [])
                        ],
                    )
                    _log_encode_timing_complete(
                        encode_run_id=encode_run_id,
                        post_id=post_id,
                        augment_ms=augment_ms,
                        build_samples_ms=build_samples_ms,
                        encode_total_ms=_elapsed_ms_since(t_encode),
                        succeeded=True,
                        retry_count=retry_count,
                        timing_outcome="success",
                    )
                    sender_audit["candidate_validation"] = {
                        "acceptance_source": "draft",
                        "group_index": accepted_candidate.get("group_index"),
                        "candidate_index": accepted_candidate.get("candidate_index"),
                        "decoded_index": accepted_candidate.get("decoded_index"),
                        "strict_decoded_index": accepted_candidate.get("strict_decoded_index"),
                    }
                    return {
                        "stego_text": stego_text,
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": True,
                        "retry_count": retry_count,
                        "tag": tag,
                        "sender_audit": sender_audit,
                        "embedding": post_augmentation,
                        "encoded_samples": encoded_results,
                        "decoded_indices": [
                            item.get("decoded_index")
                            for item in validation.get("validationDetails", {}).get("candidates", [])
                        ],
                        "validation_details": validation.get("validationDetails"),
                    }

                validation_details = validation.get("validationDetails", {})
                _stego_log_bind("validation").warning(
                    "post_id={} attempt={} failed selected_idx={} decoded_indices={}",
                    post_id,
                    retry_count + 1,
                    selected_idx,
                    [
                        item.get("decoded_index")
                        for item in validation_details.get("candidates", [])
                    ],
                )
                should_sharpen = (
                    configured_prompt_style == "natural_sharpened"
                    or retry_count >= resolved_max_retries
                )
                if should_sharpen:
                    promising_candidates = validation.get("promising_candidates") or []
                    for promising_candidate in promising_candidates:
                        group_idx = int(promising_candidate.get("group_index", 0))
                        source_group = encoded_results[group_idx]
                        sharpened_text = self._revise_candidate_text_contextually(
                            candidate_text=str(promising_candidate.get("text", "")),
                            sample=source_group,
                            comment_embedding=post_augmentation["commentEmbedding"],
                            encode_run_id=encode_run_id,
                            sample_index=group_idx,
                            llm_timings=llm_timings,
                        )
                        sharpened_group = {
                            "category": source_group.get("category"),
                            "source_quote": source_group.get("source_quote"),
                            "tangent": source_group.get("tangent"),
                            "prompt_style": "natural_sharpened",
                            "texts": [sharpened_text],
                            "generation_mode": "context_sharpen",
                        }
                        sharpen_validation = self._evaluate_candidate_groups(
                            encoded_results=[sharpened_group],
                            tangents_db=tangents_db,
                            selected_angle=selected_angle,
                            post_augmentation=post_augmentation,
                            encode_run_id=encode_run_id,
                        )
                        if sharpen_validation.get("succeeded"):
                            accepted_candidate = sharpen_validation.get("accepted_candidate") or {}
                            encoded_results.append(sharpened_group)
                            _log_encode_timing_complete(
                                encode_run_id=encode_run_id,
                                post_id=post_id,
                                augment_ms=augment_ms,
                                build_samples_ms=build_samples_ms,
                                encode_total_ms=_elapsed_ms_since(t_encode),
                                succeeded=True,
                                retry_count=retry_count,
                                timing_outcome="context_sharpen",
                            )
                            sender_audit["candidate_validation"] = {
                                "acceptance_source": "context_sharpen",
                                "group_index": accepted_candidate.get("group_index"),
                                "candidate_index": accepted_candidate.get("candidate_index"),
                                "decoded_index": accepted_candidate.get("decoded_index"),
                                "strict_decoded_index": accepted_candidate.get("strict_decoded_index"),
                            }
                            return {
                                "stego_text": str(accepted_candidate.get("text", "")),
                                "post": post,
                                "selected_angle": selected_angle,
                                "angle_index": selected_idx,
                                "succeeded": True,
                                "retry_count": retry_count,
                                "tag": tag,
                                "sender_audit": sender_audit,
                                "embedding": post_augmentation,
                                "encoded_samples": encoded_results,
                                "decoded_indices": [
                                    item.get("decoded_index")
                                    for item in sharpen_validation.get("validationDetails", {}).get("candidates", [])
                                ],
                                "validation_details": sharpen_validation.get("validationDetails"),
                            }
                if retry_count >= resolved_max_retries:
                    error_details = {
                        "reason": (
                            "No generated or context-sharpened candidate stayed context-faithful and decoded to the selected angle in strict mode."
                        ),
                        "selected_angle": _angle_summary(selected_angle),
                        "decoded_indices": [
                            item.get("decoded_index")
                            for item in validation_details.get("candidates", [])
                        ],
                        "candidate_results": validation_details.get("candidates", []),
                    }
                    _stego_log_bind("failed").error(
                        "post_id={} reason={}",
                        post_id,
                        error_details["reason"],
                    )
                    _log_encode_timing_complete(
                        encode_run_id=encode_run_id,
                        post_id=post_id,
                        augment_ms=augment_ms,
                        build_samples_ms=build_samples_ms,
                        encode_total_ms=_elapsed_ms_since(t_encode),
                        succeeded=False,
                        retry_count=retry_count,
                        timing_outcome="validation_exhausted",
                    )
                    return {
                        "stego_text": primary_texts[0] if primary_texts else "",
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": False,
                        "retry_count": retry_count,
                        "tag": tag,
                        "sender_audit": sender_audit,
                        "error": "Decoding validation failed",
                        "error_details": error_details,
                        "breakdown": last_breakdown,
                        "validation_details": validation_details,
                        "embedding": post_augmentation,
                        "encoded_samples": encoded_results,
                    }
                retry_count += 1
            except Exception as exc:
                _stego_log_bind("error").exception(
                    "post_id={} attempt={} type={}",
                    post_id,
                    retry_count + 1,
                    type(exc).__name__,
                )
                if retry_count >= resolved_max_retries:
                    _log_encode_timing_complete(
                        encode_run_id=encode_run_id,
                        post_id=post_id,
                        augment_ms=augment_ms,
                        build_samples_ms=build_samples_ms,
                        encode_total_ms=_elapsed_ms_since(t_encode),
                        succeeded=False,
                        retry_count=retry_count,
                        timing_outcome="exception",
                    )
                    return {
                        "stego_text": "",
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": False,
                        "retry_count": retry_count,
                        "tag": tag,
                        "sender_audit": sender_audit,
                        "error": str(exc),
                        "error_details": {
                            "reason": "Unexpected exception during stego encoding.",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "selected_angle": _angle_summary(selected_angle),
                        },
                        "breakdown": last_breakdown,
                        "embedding": post_augmentation,
                    }
                retry_count += 1

        raise RuntimeError("Stego encode retry loop exited unexpectedly.")

    def encode_binary_selection_bits(
        self,
        bits: str,
        post: dict[str, Any],
        tag: str | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Diagnostic encode path that scans prepared selection-channel bits directly."""
        angles = post.get("angles", [])
        if not isinstance(angles, list) or not angles:
            raise ValueError("Post must have angles")
        if set(bits) - {"0", "1"}:
            raise ValueError("Selection bits must contain only '0' and '1'")

        resolved_max_retries = (
            get_workflow_stego_default_max_retries() if max_retries is None else max_retries
        )
        post_id = post.get("id")
        encode_run_id = uuid4().hex
        t_encode = time.perf_counter()
        _stego_log_bind("start").bind(stego_encode_run_id=encode_run_id).info(
            "post_id={} binary_bits_len={} max_retries={} diagnostic_binary_selection_bits=true",
            post_id,
            len(bits),
            resolved_max_retries,
        )

        t_aug = time.perf_counter()
        post_augmentation = codec_augment_post_with_selection_bits(bits, post)
        sender_audit = _sender_audit_from_post(post, post_augmentation)
        sender_audit["encoding"] = get_workflow_encoding_settings()
        sender_audit["payload_transform"] = "diagnostic_binary_selection_bits"
        sender_audit["payload_carrier"] = "selection_channel"
        sender_audit["raw_payload_bytes"] = 0
        sender_audit["embedded_payload_bytes"] = 0
        sender_audit["binary_selection_bits"] = bits
        sender_audit["compression_skipped"] = True
        sender_audit["payload_transform_skipped"] = True
        llm_timings: list[dict[str, Any]] = []
        sender_audit["llm_timings"] = llm_timings
        post_augmentation["senderAudit"] = sender_audit
        augment_ms = _elapsed_ms_since(t_aug)

        selected_angle = post_augmentation["angleEmbedding"].get("selectedAngle", {})
        selected_idx = selected_angle.get("idx")
        t_samp = time.perf_counter()
        samples, tangents_db = self._build_samples(post_augmentation, post)
        build_samples_ms = _elapsed_ms_since(t_samp)
        if not samples:
            _log_encode_timing_complete(
                encode_run_id=encode_run_id,
                post_id=post_id,
                augment_ms=augment_ms,
                build_samples_ms=build_samples_ms,
                encode_total_ms=_elapsed_ms_since(t_encode),
                succeeded=False,
                retry_count=0,
                timing_outcome="no_samples",
            )
            return {
                "stego_text": "",
                "post": post,
                "selected_angle": selected_angle,
                "angle_index": selected_idx,
                "succeeded": False,
                "retry_count": 0,
                "tag": tag,
                "sender_audit": sender_audit,
                "error": "No samples generated from angle embedding",
                "embedding": post_augmentation,
                "binary_selection_bits": bits,
                "comment_bits": post_augmentation.get("commentBits", ""),
                "angle_bits": post_augmentation.get("angleBits", ""),
                "compression_skipped": True,
                "payload_transform_skipped": True,
            }

        retry_count = 0
        configured_prompt_style = str(get_workflow_stego_prompt_style())
        while retry_count <= resolved_max_retries:
            try:
                prompt_style = _prompt_style_for_attempt(configured_prompt_style, retry_count)
                encoded_results: list[dict[str, Any]] = []
                for sidx, sample in enumerate(samples):
                    texts = self._generate_stego_texts(
                        sample=sample,
                        comment_embedding=post_augmentation["commentEmbedding"],
                        prompt_style=prompt_style,
                        sample_index=sidx,
                        encode_run_id=encode_run_id,
                        llm_timings=llm_timings,
                    )
                    if _eq_angle(sample, selected_angle):
                        texts = _with_selected_angle_anchor_variants(texts, selected_angle)
                    encoded_results.append(
                        {
                            "category": sample.get("category"),
                            "source_quote": sample.get("source_quote"),
                            "tangent": sample.get("tangent"),
                            "prompt_style": prompt_style,
                            "texts": texts,
                        }
                    )
                primary_texts = encoded_results[0].get("texts", []) if encoded_results else []
                if not primary_texts:
                    raise RuntimeError("Encoder did not return candidate texts")

                validation = self._evaluate_candidate_groups(
                    encoded_results=encoded_results,
                    tangents_db=tangents_db,
                    selected_angle=selected_angle,
                    post_augmentation=post_augmentation,
                    encode_run_id=encode_run_id,
                )
                if validation.get("succeeded"):
                    accepted_candidate = validation.get("accepted_candidate") or {}
                    stego_text = str(accepted_candidate.get("text", ""))
                    sender_audit["candidate_validation"] = {
                        "acceptance_source": "draft",
                        "group_index": accepted_candidate.get("group_index"),
                        "candidate_index": accepted_candidate.get("candidate_index"),
                        "decoded_index": accepted_candidate.get("decoded_index"),
                        "strict_decoded_index": accepted_candidate.get("strict_decoded_index"),
                    }
                    _log_encode_timing_complete(
                        encode_run_id=encode_run_id,
                        post_id=post_id,
                        augment_ms=augment_ms,
                        build_samples_ms=build_samples_ms,
                        encode_total_ms=_elapsed_ms_since(t_encode),
                        succeeded=True,
                        retry_count=retry_count,
                        timing_outcome="diagnostic_success",
                    )
                    return {
                        "stego_text": stego_text,
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": True,
                        "retry_count": retry_count,
                        "tag": tag,
                        "sender_audit": sender_audit,
                        "embedding": post_augmentation,
                        "encoded_samples": encoded_results,
                        "decoded_indices": [
                            item.get("decoded_index")
                            for item in validation.get("validationDetails", {}).get("candidates", [])
                        ],
                        "validation_details": validation.get("validationDetails"),
                        "binary_selection_bits": bits,
                        "comment_bits": post_augmentation.get("commentBits", ""),
                        "angle_bits": post_augmentation.get("angleBits", ""),
                        "compression_skipped": True,
                        "payload_transform_skipped": True,
                    }

                validation_details = validation.get("validationDetails", {})
                if retry_count >= resolved_max_retries:
                    _log_encode_timing_complete(
                        encode_run_id=encode_run_id,
                        post_id=post_id,
                        augment_ms=augment_ms,
                        build_samples_ms=build_samples_ms,
                        encode_total_ms=_elapsed_ms_since(t_encode),
                        succeeded=False,
                        retry_count=retry_count,
                        timing_outcome="diagnostic_validation_exhausted",
                    )
                    return {
                        "stego_text": primary_texts[0] if primary_texts else "",
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": False,
                        "retry_count": retry_count,
                        "tag": tag,
                        "sender_audit": sender_audit,
                        "error": "Decoding validation failed",
                        "error_details": {
                            "reason": "Diagnostic candidate did not decode to selected angle.",
                            "selected_angle": _angle_summary(selected_angle),
                            "candidate_results": validation_details.get("candidates", []),
                        },
                        "validation_details": validation_details,
                        "embedding": post_augmentation,
                        "encoded_samples": encoded_results,
                        "binary_selection_bits": bits,
                        "comment_bits": post_augmentation.get("commentBits", ""),
                        "angle_bits": post_augmentation.get("angleBits", ""),
                        "compression_skipped": True,
                        "payload_transform_skipped": True,
                    }
                retry_count += 1
            except Exception as exc:
                if retry_count >= resolved_max_retries:
                    _log_encode_timing_complete(
                        encode_run_id=encode_run_id,
                        post_id=post_id,
                        augment_ms=augment_ms,
                        build_samples_ms=build_samples_ms,
                        encode_total_ms=_elapsed_ms_since(t_encode),
                        succeeded=False,
                        retry_count=retry_count,
                        timing_outcome="diagnostic_exception",
                    )
                    return {
                        "stego_text": "",
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": False,
                        "retry_count": retry_count,
                        "tag": tag,
                        "sender_audit": sender_audit,
                        "error": str(exc),
                        "error_details": {
                            "reason": "Unexpected exception during diagnostic stego encoding.",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "selected_angle": _angle_summary(selected_angle),
                        },
                        "embedding": post_augmentation,
                        "binary_selection_bits": bits,
                        "comment_bits": post_augmentation.get("commentBits", ""),
                        "angle_bits": post_augmentation.get("angleBits", ""),
                        "compression_skipped": True,
                        "payload_transform_skipped": True,
                    }
                retry_count += 1

        raise RuntimeError("Diagnostic stego encode retry loop exited unexpectedly.")

    def process_post(
        self,
        post_id: str | None = None,
        payload: str | None = None,
        tag: str | None = None,
        step: str = "final-step",
        list_offset: int = STEGO_DEFAULT_OFFSET,
    ) -> dict[str, Any]:
        """Process one post and persist output on success.

        If post_id is not provided, select one unprocessed final-step post using tag.
        If payload is not provided, load default payload/tag from Stego workflow JSON.
        """

        def _select_next_post_id() -> str:
            posts_list = self.backend.posts_list(
                step="final-step",
                count=1,
                offset=max(0, int(list_offset)),
                tag=resolved_tag,
            )
            file_names = posts_list.get("fileNames", [])
            if not file_names:
                raise ValueError(
                    f"No unprocessed posts found for step='final-step' and tag='{resolved_tag}'."
                )
            first_file = file_names[0]
            next_post_id = first_file[:-5] if first_file.endswith(".json") else first_file
            _stego_log_bind("process", process_event="auto_selected").info(
                "post_id={} for tag={}",
                next_post_id,
                resolved_tag,
            )
            return next_post_id

        process_run_id = uuid4().hex
        t_process = time.perf_counter()
        _stego_log_bind("process", process_event="start").bind(
            stego_process_run_id=process_run_id,
        ).info(
            "post_id={} list_offset={}",
            post_id,
            list_offset,
        )
        workflow_payload, workflow_tag = self._load_default_payload_and_tag()
        using_workflow_payload = not (isinstance(payload, str) and payload)
        resolved_payload = payload if isinstance(payload, str) and payload else workflow_payload
        resolved_tag = (
            tag if tag is not None else (workflow_tag if using_workflow_payload else None)
        )

        if not resolved_payload:
            raise ValueError(
                "Payload is required. Provide payload or configure SetSecretData payload in workflows/27rZrYtywu3k9e7Q.json."
            )

        resolved_post_id = post_id
        if not resolved_post_id:
            resolved_post_id = _select_next_post_id()

        # n8n Stego reads post data from final-step; keep fallback for local compatibility.
        try:
            post = self.backend.get_post_local(f"{resolved_post_id}.json", step="final-step")
        except FileNotFoundError:
            try:
                post = self.backend.get_post_local(f"{resolved_post_id}.json", step="angles-step")
            except FileNotFoundError:
                # If caller passed an outdated/nonexistent post_id, keep API parity with n8n:
                # pick next unprocessed post for the same tag instead of hard-failing.
                if post_id:
                    _stego_log_bind("process", process_event="fallback_post").warning(
                        "post_id={} not found; falling back to next unprocessed for tag={}",
                        resolved_post_id,
                        resolved_tag,
                    )
                    resolved_post_id = _select_next_post_id()
                    try:
                        post = self.backend.get_post_local(
                            f"{resolved_post_id}.json", step="final-step"
                        )
                    except FileNotFoundError:
                        post = self.backend.get_post_local(
                            f"{resolved_post_id}.json", step="angles-step"
                        )
                else:
                    raise

        result = self.encode(payload=resolved_payload, post=post, tag=resolved_tag)
        result_post_id = str(post.get("id") or resolved_post_id)
        filename = (
            f"{result_post_id}_{resolved_tag}.json" if resolved_tag else f"{result_post_id}.json"
        )
        stego_text = result.get("stego_text")
        should_save = bool(result.get("succeeded")) and _is_non_empty_string(stego_text)
        if should_save:
            artifact = n8n_save_object_body(result)
            assert_valid_n8n_stego_artifact(artifact)
            # Keep parity with n8n workflow: write final output artifact into ./output-results.
            self.backend.save_object_local(artifact, step="final-step", filename=filename)
            _stego_log_bind("process", process_event="saved").info(
                "post_id={} step={} filename={}",
                result_post_id,
                "final-step",
                filename,
            )
        else:
            missing_state = "missing"
            if isinstance(stego_text, str):
                missing_state = "empty" if not stego_text.strip() else "present"
            _stego_log_bind("process", process_event="skipped_artifact").error(
                "post_id={} succeeded={} stego_text_state={} error={}",
                result_post_id,
                bool(result.get("succeeded")),
                missing_state,
                result.get("error"),
            )

        if not result.get("succeeded"):
            _stego_log_bind("process", process_event="failed").error(
                "post_id={} error={}",
                resolved_post_id,
                result.get("error"),
            )
        proc_ms = _elapsed_ms_since(t_process)
        _stego_log_bind("timing", timing_phase="process_post_complete", log_op="process").bind(
            stego_process_run_id=process_run_id,
            elapsed_ms=proc_ms,
            succeeded=bool(result.get("succeeded")),
        ).info(
            "post_id={} elapsed_ms={} succeeded={}",
            str(post.get("id") or resolved_post_id),
            proc_ms,
            bool(result.get("succeeded")),
        )
        return result
