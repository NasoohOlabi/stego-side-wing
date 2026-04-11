"""Steganographic encoding pipeline with n8n parity logic."""
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from infrastructure.config import resolve_workflow_llm_provider_and_model
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.llm_temperatures import STEGO_CYCLE_LLM_TEMPERATURE
from workflows.pipelines.decode import DECODE_LLM_MODEL, DecodePipeline
from loguru import logger

from workflows.utils import stego_codec
from workflows.utils.output_results_shape import (
    assert_valid_n8n_stego_artifact,
    n8n_save_object_body,
)
from workflows.utils.stego_codec import (
    augment_post as codec_augment_post,
    build_dictionary as codec_build_dictionary,
    compress_payload as codec_compress_payload,
    embed_in_angle_selection as codec_embed_in_angle_selection,
    embed_in_comment_selection as codec_embed_in_comment_selection,
    flatten_comments,
)
from workflows.utils.workflow_llm_prompts import get_prompts

# Backward-compatible names for tests and callers.
MAX_LITERAL_LEN = stego_codec.MAX_LITERAL_LEN
_is_non_empty_string = stego_codec.is_non_empty_string
_flatten_comments = flatten_comments
_get_bit_width = stego_codec.get_bit_width
_take_bits = stego_codec.take_bits

STEGO_WORKFLOW_ID = "27rZrYtywu3k9e7Q"
STEGO_DEFAULT_OFFSET = 1
STEGO_LLM_MODEL = DECODE_LLM_MODEL
# Headroom for models that emit thinking in-content before the JSON array.
STEGO_ENCODE_MAX_TOKENS = 1536
_STEGO_LOG = logger.bind(component="StegoPipeline")


def _elapsed_ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _stego_log_bind(
    log_area: str,
    *,
    log_op: str = "encode",
    prompt_role: Optional[str] = None,
    llm_stage: Optional[str] = None,
    process_event: Optional[str] = None,
    timing_phase: Optional[str] = None,
) -> Any:
    """Structured stego log context (log_domain / log_area / log_op); message stays prefix-free."""
    fields: Dict[str, Any] = {
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


def _stego_clean_json_string_list(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if isinstance(x, str) and str(x).strip()]


def _stego_comment_strings_from_parsed(parsed: Any) -> Optional[List[str]]:
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


def _eq_angle(lhs: Optional[Dict[str, Any]], rhs: Optional[Dict[str, Any]]) -> bool:
    if lhs is None and rhs is None:
        return True
    if lhs is None or rhs is None:
        return False
    return (
        lhs.get("category") == rhs.get("category")
        and lhs.get("tangent") == rhs.get("tangent")
        and lhs.get("source_quote") == rhs.get("source_quote")
    )


def _angle_summary(angle: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(angle, dict):
        return None
    return {
        "idx": angle.get("idx"),
        "category": angle.get("category"),
        "tangent": angle.get("tangent"),
        "source_quote": angle.get("source_quote"),
        "source_document": angle.get("source_document"),
    }


def _text_preview(text: Any, max_len: int = 180) -> str:
    if not isinstance(text, str):
        return ""
    stripped = " ".join(text.split())
    return stripped if len(stripped) <= max_len else f"{stripped[:max_len]}..."


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

    def _load_default_payload_and_tag(self) -> Tuple[Optional[str], Optional[str]]:
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

        payload_value: Optional[str] = None
        for node in nodes:
            if not isinstance(node, dict) or node.get("name") != "SetSecretData":
                continue
            assignments = (
                node.get("parameters", {})
                .get("assignments", {})
                .get("assignments", [])
            )
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

    def _build_dictionary(self, post: Dict[str, Any]) -> List[str]:
        return codec_build_dictionary(post)

    def _compress_payload(self, payload: str, dictionary: List[str]) -> Dict[str, Any]:
        return codec_compress_payload(payload, dictionary)

    def _embed_in_comment_selection(
        self, bits: str, post: Dict[str, Any]
    ) -> Dict[str, Any]:
        return codec_embed_in_comment_selection(bits, post)

    def _embed_in_angle_selection(
        self, bits: str, nested_angles: List[List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        return codec_embed_in_angle_selection(bits, nested_angles)

    def _augment_post(self, payload: str, post: Dict[str, Any]) -> Dict[str, Any]:
        return codec_augment_post(payload, post)

    def _build_samples(
        self, post_augmentation: Dict[str, Any], post: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        angle_embedding = post_augmentation.get("angleEmbedding", {})
        candidate_angles = angle_embedding.get("totalAnglesSelectedFirst", [])[:4]
        needles = [
            str(a.get("source_quote", ""))
            for a in candidate_angles
            if isinstance(a, dict)
        ]
        haystack = post.get("search_results", [])
        if not isinstance(haystack, list):
            haystack = []

        source_response = self.backend.needle_finder_batch(needles=needles, haystack=haystack)
        source_results = source_response.get("results", [])

        samples: List[Dict[str, Any]] = []
        for idx, angle in enumerate(candidate_angles):
            if not isinstance(angle, dict):
                continue
            match_data = source_results[idx] if idx < len(source_results) else {}
            best_match = (
                match_data.get("best_match", "")
                if isinstance(match_data, dict)
                else ""
            )
            sample = dict(angle)
            sample["best_match"] = best_match
            samples.append(sample)

        tangents_db = angle_embedding.get("TangentsDB", [])
        if not isinstance(tangents_db, list):
            tangents_db = []
        return samples, tangents_db

    def _build_prompt(
        self, sample: Dict[str, Any], comment_embedding: Dict[str, Any]
    ) -> Tuple[str, str]:
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
            rendered: List[str] = []
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
                label = "commented" if not rendered else "replyed"
                rendered.append(f"{name} {label}:\n{body}")
            if rendered:
                chain_section = "\n---\n" + "\n---\n".join(rendered)

        enc = get_prompts().stego_encode
        prompt = enc.user_template.format(
            best_match=str(sample.get("best_match", "")),
            title=title,
            author=author,
            selftext=selftext,
            chain_section=chain_section,
        )
        system_message = enc.system_template.format(
            tangent=str(sample.get("tangent", "")),
            category=str(sample.get("category", "")),
        )
        return prompt, system_message

    def _generate_stego_texts(
        self,
        sample: Dict[str, Any],
        comment_embedding: Dict[str, Any],
        *,
        sample_index: int = 0,
        encode_run_id: str = "",
    ) -> List[str]:
        def _extract_json_block(raw: str) -> str:
            stripped = raw.strip()
            if not stripped.startswith("```"):
                return stripped
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                return "\n".join(lines[1:-1]).strip()
            return stripped

        prompt, system_message = self._build_prompt(sample, comment_embedding)
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
            temperature=STEGO_CYCLE_LLM_TEMPERATURE,
            max_tokens=STEGO_ENCODE_MAX_TOKENS,
        )
        llm_wall_ms = _elapsed_ms_since(t_llm)
        meta = self.llm.last_call_metadata or {}
        llm_adapter_ms = meta.get("elapsed_ms")
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

    def _cross_validate(
        self,
        candidate_texts: List[str],
        few_shots: List[Dict[str, Any]],
        tangents_db: List[Dict[str, Any]],
        selected_angle: Dict[str, Any],
        *,
        encode_run_id: str = "",
    ) -> Dict[str, Any]:
        t_cv = time.perf_counter()
        decoded_indices: List[Optional[int]] = []
        decodeds: List[Optional[Dict[str, Any]]] = []
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

        validation_candidates: List[Dict[str, Any]] = []
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

        breakdown: Dict[str, Any] = {}
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
        post: Dict[str, Any],
        tag: Optional[str] = None,
        max_retries: int = 4,
    ) -> Dict[str, Any]:
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

        post_id = post.get("id")
        encode_run_id = uuid4().hex
        t_encode = time.perf_counter()
        _stego_log_bind("start").bind(stego_encode_run_id=encode_run_id).info(
            "post_id={} payload_len={} max_retries={}",
            post_id,
            len(payload),
            max_retries,
        )

        t_aug = time.perf_counter()
        post_augmentation = self._augment_post(payload, post)
        augment_ms = _elapsed_ms_since(t_aug)
        _stego_log_bind("timing", timing_phase="augment_post").bind(
            stego_encode_run_id=encode_run_id,
            elapsed_ms=augment_ms,
        ).info("post_id={} elapsed_ms={}", post_id, augment_ms)

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
                "error": "No samples generated from angle embedding",
                "error_details": {
                    "reason": "Angle embedding produced zero sample prompts for generation.",
                    "selected_angle": _angle_summary(
                        post_augmentation.get("angleEmbedding", {}).get("selectedAngle")
                    ),
                },
                "embedding": post_augmentation,
            }

        selected_angle = post_augmentation["angleEmbedding"].get("selectedAngle", {})
        selected_idx = selected_angle.get("idx")
        retry_count = 0
        last_breakdown: Dict[str, Any] = {}

        while retry_count <= max_retries:
            try:
                t_attempt = time.perf_counter()
                _stego_log_bind("attempt").info(
                    "post_id={} attempt={}/{} selected_idx={}",
                    post_id,
                    retry_count + 1,
                    max_retries + 1,
                    selected_idx,
                )
                encoded_results: List[Dict[str, Any]] = []
                t_gen = time.perf_counter()
                for sidx, sample in enumerate(samples):
                    texts = self._generate_stego_texts(
                        sample=sample,
                        comment_embedding=post_augmentation["commentEmbedding"],
                        sample_index=sidx,
                        encode_run_id=encode_run_id,
                    )
                    encoded_results.append(
                        {
                            "category": sample.get("category"),
                            "source_quote": sample.get("source_quote"),
                            "tangent": sample.get("tangent"),
                            "texts": texts,
                        }
                    )
                generate_ms = _elapsed_ms_since(t_gen)

                primary_texts = encoded_results[0].get("texts", []) if encoded_results else []
                few_shots = encoded_results[1:]
                if not primary_texts:
                    raise RuntimeError("Encoder did not return candidate texts")

                _stego_log_bind("generate").info(
                    "post_id={} attempt={} primary_candidates={} few_shot_groups={}",
                    post_id,
                    retry_count + 1,
                    len(primary_texts),
                    len(few_shots),
                )
                t_val = time.perf_counter()
                validation = self._cross_validate(
                    candidate_texts=primary_texts,
                    few_shots=few_shots,
                    tangents_db=tangents_db,
                    selected_angle=selected_angle,
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
                    stego_text = validation.get("stegoText")
                    if not _is_non_empty_string(stego_text):
                        raise RuntimeError(
                            "Cross-validation reported success with empty stego text."
                        )
                    _stego_log_bind("success").info(
                        "post_id={} attempt={} success_candidate={} decoded_indices={}",
                        post_id,
                        retry_count + 1,
                        validation.get("successIdx"),
                        validation.get("decodedIndices", []),
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
                    return {
                        "stego_text": stego_text,
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": True,
                        "retry_count": retry_count,
                        "tag": tag,
                        "embedding": post_augmentation,
                        "encoded_samples": encoded_results,
                        "decoded_indices": validation.get("decodedIndices", []),
                        "validation_details": validation.get("validationDetails"),
                    }

                last_breakdown = validation.get("breakDown", {})
                validation_details = validation.get("validationDetails", {})
                _stego_log_bind("validation").warning(
                    "post_id={} attempt={} failed selected_idx={} decoded_indices={}",
                    post_id,
                    retry_count + 1,
                    selected_idx,
                    validation.get("decodedIndices", []),
                )
                if retry_count >= max_retries:
                    error_details = {
                        "reason": (
                            "None of the generated primary candidate texts decoded to the selected angle."
                        ),
                        "selected_angle": _angle_summary(selected_angle),
                        "decoded_indices": validation.get("decodedIndices", []),
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
                if retry_count >= max_retries:
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

    def process_post(
        self,
        post_id: Optional[str] = None,
        payload: Optional[str] = None,
        tag: Optional[str] = None,
        step: str = "final-step",
        list_offset: int = STEGO_DEFAULT_OFFSET,
    ) -> Dict[str, Any]:
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
        resolved_tag = tag if tag is not None else (workflow_tag if using_workflow_payload else None)

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
            f"{result_post_id}_{resolved_tag}.json"
            if resolved_tag
            else f"{result_post_id}.json"
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
