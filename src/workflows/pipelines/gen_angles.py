"""Generate angles from post content."""

import re
import time
from typing import Any

from loguru import logger

from infrastructure.config import (
    WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED,
    get_workflow_angles_generation_mode,
    get_workflow_angles_max_input_blocks,
    get_workflow_angles_max_output,
    get_workflow_angles_raw_target,
    get_workflow_angles_raw_target_multiplier,
    get_workflow_context_comment_weight,
    get_workflow_context_global_fallback,
    get_workflow_context_include_children,
    get_workflow_context_max_ancestors,
    get_workflow_context_research_weight,
    get_workflow_context_sampler,
    get_workflow_dictionary_max_comments,
    get_workflow_dictionary_max_search_results,
    get_workflow_llm_backend,
    get_workflow_tangent_db_builder,
    resolve_workflow_llm_provider_and_model,
)
from infrastructure.json_logging import get_trace_id
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.utils.angle_artifact import (
    ANGLE_ARTIFACT_NAMESPACE,
    ANGLE_ARTIFACT_SCHEMA_VERSION,
    ANGLE_GENERATOR_VERSION,
    CONTEXT_ANGLE_ARTIFACT_NAMESPACE,
)
from workflows.utils.angles_llm_config import (
    SYSTEM_PROMPT as ANGLES_SYSTEM_PROMPT,
)
from workflows.utils.angles_llm_config import (
    TEMPERATURE as ANGLES_TEMPERATURE,
)
from workflows.utils.angles_llm_config import (
    USER_PROMPT_TEMPLATE as ANGLES_USER_PROMPT_TEMPLATE,
)
from workflows.utils.angles_llm_config import (
    angles_model_name,
)
from workflows.utils.context_sampler import (
    ContextSamplerConfig,
    build_context_dictionary_bundle,
)
from workflows.utils.naturalness_gate import (
    filter_angles_for_post,
    naturalness_gate_enabled,
)
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.stego_codec import comment_selection_choice_count
from workflows.utils.tangent_db import (
    AngleCandidate,
    PostContext,
    build_tangent_db,
    tangent_db_config_from_env,
)
from workflows.utils.text_utils import (
    build_post_text_dictionary_bundle,
    flatten_comments,
    parse_json_array_response,
)
from workflows.utils.workflow_llm_prompts import get_prompts

_LOG = logger.bind(component="GenAnglesPipeline")


def _gen_angles_bind_log():
    tid = get_trace_id()
    return _LOG.bind(trace_id=tid if tid else "")


def _elapsed_ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


def _angle_target_report(
    angles: list[dict[str, Any]], target: int
) -> dict[str, int | bool | None]:
    if target == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED:
        return {
            "angles_target_reached": None,
            "angles_target_shortfall": None,
        }
    return {
        "angles_target_reached": len(angles) >= target,
        "angles_target_shortfall": max(0, target - len(angles)),
    }


def _finalize_angles(
    *,
    post: dict[str, Any],
    entries: list[dict[str, Any]],
    angles: list[dict[str, Any]],
    target: int,
    raw_target: int | None,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply the shared post-generation stages before enforcing the retained target."""
    selected = _apply_tangent_db_builder(
        post=post,
        entries=entries,
        angles=angles,
        max_output=raw_target if raw_target is not None else target,
        report=report,
    )
    selected = _apply_angle_relevance_gate(post=post, angles=selected, report=report)
    selected = _dedupe_angles(selected)
    return selected[:target]


def _angle_artifact_metadata(report: dict[str, Any]) -> dict[str, Any]:
    """Version metadata that lets readers distinguish refactor output from legacy posts."""
    metadata = {
        "schema_version": (
            3 if report.get("input_sampler_version") == "context_weighted_v2"
            else ANGLE_ARTIFACT_SCHEMA_VERSION
        ),
        "artifact_namespace": (
            CONTEXT_ANGLE_ARTIFACT_NAMESPACE
            if report.get("input_sampler_version") == "context_weighted_v2"
            else ANGLE_ARTIFACT_NAMESPACE
        ),
        "generator_version": ANGLE_GENERATOR_VERSION,
        "sampler_version": report.get("input_sampler_version"),
        "selection_strategy": report.get("input_selection_strategy"),
        "dictionary_id": report.get("dictionary_id"),
        "capacity_profile": report.get("input_capacity_profile"),
        "capacity_limits": report.get("input_capacity_limits"),
        "generation_mode": report.get("generation_mode"),
        "angles_retained_target": report.get("angles_retained_target"),
        "angles_raw_target": report.get("angles_raw_target"),
        "raw_target_multiplier": report.get("raw_target_multiplier"),
        "angles_target_reached": report.get("angles_target_reached"),
        "angles_target_shortfall": report.get("angles_target_shortfall"),
    }
    if report.get("input_sampler_version") == "context_weighted_v2":
        metadata.update(
            {
                "selected_parent_id": report.get("selected_parent_id"),
                "frozen_research_hash": report.get("frozen_research_hash"),
                "relationship_counts": report.get("relationship_counts"),
                "requested_allocations": report.get("requested_allocations"),
                "effective_allocations": report.get("effective_allocations"),
                "tangent_hash": report.get("angles_hash"),
                "tangent_count": report.get("tangent_count"),
                "parent_recoverable_width": report.get("parent_recoverable_width"),
                "tangent_recoverable_width": report.get("tangent_recoverable_width"),
                "post_id": report.get("post_id"),
            }
        )
    tangent_report = report.get("tangent_db_report")
    if isinstance(tangent_report, dict):
        metadata["tangent_db"] = {
            "builder_version": tangent_report.get("builder_version"),
            "config_hash": tangent_report.get("config_hash"),
            "kept_count": tangent_report.get("kept_count"),
            "revamped_tangents": True,
        }
    return metadata


def _probe_llm_run_id(pipeline: Any) -> str:
    llm = getattr(pipeline, "llm", None)
    if llm is None:
        return ""
    meta = getattr(llm, "last_call_metadata", {}) or {}
    return str(meta.get("run_id") or "")


def _source_category(source: str) -> str:
    if source == "comments":
        return "Community Discussion"
    if source == "post":
        return "Original Post"
    return "Reference Material"


def _sentence_candidates(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    cleaned: list[str] = []
    for chunk in chunks:
        sentence = " ".join(chunk.split()).strip(" -")
        if 24 <= len(sentence) <= 220:
            cleaned.append(sentence)
    return cleaned or ([" ".join(text.split())[:220].strip()] if text.strip() else [])


def _entry_to_angles(entry: dict[str, Any], source_document: int) -> list[dict[str, Any]]:
    text = str(entry.get("text", "")).strip()
    if not text:
        return []
    return [
        {
            "source_quote": sentence,
            "tangent": sentence,
            "category": _source_category(str(entry.get("source", ""))),
            "source_document": source_document,
        }
        for sentence in _sentence_candidates(text)[:2]
    ]


def _dedupe_angles(angles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for angle in angles:
        key = (
            str(angle.get("source_quote", "")).casefold(),
            str(angle.get("tangent", "")).casefold(),
            str(angle.get("category", "")).casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(angle)
    return out


def _candidate_for_entry(angle: dict[str, Any], entries: list[dict[str, Any]]) -> AngleCandidate:
    candidate = AngleCandidate.from_angle(angle)
    index = candidate.source_document
    if 0 <= index < len(entries):
        return candidate.model_copy(update={"source": str(entries[index].get("source", ""))})
    return candidate


def _apply_tangent_db_builder(
    *,
    post: dict[str, Any],
    entries: list[dict[str, Any]],
    angles: list[dict[str, Any]],
    max_output: int,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    if get_workflow_tangent_db_builder() != "v1":
        return angles
    candidates = [_candidate_for_entry(angle, entries) for angle in angles]
    result = build_tangent_db(
        candidates, PostContext.from_post(post), tangent_db_config_from_env(max_output)
    )
    tangent_report = result.report.model_dump(mode="json")
    report["tangent_db_report"] = tangent_report
    _gen_angles_bind_log().info(
        "tangent_db_selection_complete",
        post_id=post.get("id"),
        input_count=tangent_report["input_candidate_count"],
        kept_count=tangent_report["kept_count"],
        dropped=tangent_report["dropped"],
        config_hash=tangent_report["config_hash"],
    )
    return result.angles


def _post_with_angles(
    post: dict[str, Any], angles: list[dict[str, Any]], report: dict[str, Any]
) -> dict[str, Any]:
    report["post_id"] = str(post.get("id") or "<unknown>")
    report["tangent_count"] = len(angles)
    report["parent_recoverable_width"] = (
        comment_selection_choice_count(post).bit_length() - 1
    )
    report["tangent_recoverable_width"] = len(angles).bit_length() - 1
    processed = dict(
        post,
        angles=angles,
        options_count=len(angles),
        angle_artifact=_angle_artifact_metadata(report),
    )
    tangent_report = report.get("tangent_db_report")
    if isinstance(tangent_report, dict):
        processed["tangent_db_report"] = tangent_report
    return processed


def _apply_angle_relevance_gate(
    *,
    post: dict[str, Any],
    angles: list[dict[str, Any]],
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    if not naturalness_gate_enabled():
        report["angle_relevance_gate"] = {"enabled": False}
        return angles
    filtered, gate_report = filter_angles_for_post(angles, post)
    report["angle_relevance_gate"] = gate_report
    _gen_angles_bind_log().info(
        "angle_relevance_gate_applied",
        post_id=post.get("id"),
        input_count=gate_report["input_count"],
        kept_count=gate_report["kept_count"],
        rejected_count=gate_report["rejected_count"],
        reason_counts=gate_report["reason_counts"],
    )
    return filtered


class GenAnglesPipeline:
    """
    Stateful orchestration for angle generation: owns backend and LLM adapters, workflow
    config, and the last batch processing summary for observability and CLI/API callers.
    """

    def __init__(
        self,
        *,
        backend: BackendAPIAdapter | None = None,
        llm: LLMAdapter | None = None,
    ) -> None:
        self.backend = backend or BackendAPIAdapter()
        self.llm = llm or LLMAdapter()
        self.config = get_config()
        self._last_batch_summary: dict[str, Any] = {}

    def _flatten_comments(self, comments: list[dict]) -> list[dict]:
        """Flatten nested comment structure."""
        return flatten_comments(comments)

    def _build_dictionary(self, post: dict) -> list[str]:
        """Build dictionary of texts from post."""
        return list(self.build_dictionary_bundle_for_post(post)["texts"])

    def build_dictionary_for_post(self, post: dict[str, Any]) -> list[str]:
        """Public alias for workflow runner / tools that need the same inputs as gen_angles."""
        return self._build_dictionary(post)

    def build_dictionary_bundle_for_post(
        self, post: dict[str, Any], *, selected_parent_id: str | None = None
    ) -> dict[str, Any]:
        """Texts plus deterministic dictionary observability metadata."""
        if get_workflow_context_sampler() == "context_weighted_v2":
            config = ContextSamplerConfig(
                max_blocks=get_workflow_angles_max_input_blocks(),
                comment_cap=get_workflow_dictionary_max_comments(),
                research_cap=get_workflow_dictionary_max_search_results(),
                comment_weight=get_workflow_context_comment_weight(),
                research_weight=get_workflow_context_research_weight(),
                max_ancestors=get_workflow_context_max_ancestors(),
                include_children=get_workflow_context_include_children(),
                global_fallback=get_workflow_context_global_fallback(),
            )
            return build_context_dictionary_bundle(post, selected_parent_id, config)
        return build_post_text_dictionary_bundle(post, apply_capacity_profile=True)

    def _generate_angles_extractive(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        angles: list[dict[str, Any]] = []
        for source_document, entry in enumerate(entries):
            angles.extend(_entry_to_angles(entry, source_document))
        return _dedupe_angles(angles)

    def preview_post(
        self,
        post: dict[str, Any],
        allow_fallback: bool = False,
        *,
        selected_parent_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate angles without mutating or saving artifacts."""
        post_id = str(post.get("id") or "<unknown>")
        dictionary_bundle = self.build_dictionary_bundle_for_post(
            post, selected_parent_id=selected_parent_id
        )
        entry_bundle = list(dictionary_bundle.get("entries", []))
        dictionary = list(dictionary_bundle["texts"])
        dictionary_report = dict(dictionary_bundle["report"])
        generation_mode = get_workflow_angles_generation_mode()
        t_preview = time.perf_counter()
        cfg = getattr(self, "config", None) or get_config()
        if get_workflow_llm_backend() == "google":
            wf_provider, wf_model = resolve_workflow_llm_provider_and_model(
                cfg.model or "mistral-nemo-instruct-2407-abliterated"
            )
            report_provider, report_model = wf_provider, wf_model
        else:
            report_provider, report_model = "lm_studio", angles_model_name()
        report = {
            "post_id": post_id,
            "input_count": len(dictionary),
            "input_hash": stable_hash(dictionary),
            "input_items": [
                {
                    "index": idx,
                    "length": len(text),
                    "hash": stable_hash(text),
                    "preview": text_preview(text),
                }
                for idx, text in enumerate(dictionary)
            ],
            "provider": report_provider,
            "model": report_model,
            "temperature": ANGLES_TEMPERATURE,
            "generation_mode": generation_mode,
            "artifact_schema_version": ANGLE_ARTIFACT_SCHEMA_VERSION,
            "artifact_namespace": ANGLE_ARTIFACT_NAMESPACE,
            "angle_generator_version": ANGLE_GENERATOR_VERSION,
            "system_prompt_hash": stable_hash(ANGLES_SYSTEM_PROMPT),
            "user_prompt_template_hash": stable_hash(ANGLES_USER_PROMPT_TEMPLATE),
            "used_fallback": False,
            "angles_retained_target": (
                None
                if get_workflow_angles_max_output()
                == WORKFLOW_CAPACITY_EFFECTIVELY_UNBOUNDED
                else get_workflow_angles_max_output()
            ),
            "angles_raw_target": get_workflow_angles_raw_target(),
            "raw_target_multiplier": get_workflow_angles_raw_target_multiplier(),
            "dictionary_id": dictionary_report["dictionary_id"],
            "input_raw_count": dictionary_report["raw_entry_count"],
            "input_source_counts": dictionary_report["source_counts"],
            "input_raw_source_counts": dictionary_report["raw_source_counts"],
            "input_capacity_applied": dictionary_report["capacity_applied"],
            "input_truncated_sources": dictionary_report["truncated_sources"],
            "input_capacity_profile": dictionary_report["capacity_profile"],
            "input_capacity_limits": dictionary_report["capacity_limits"],
            "input_sampler_version": dictionary_report.get("sampler_version"),
            "input_selection_strategy": dictionary_report.get("selection_strategy"),
            "input_sample_entries": dictionary_report["sample_entries"],
            "selected_parent_id": dictionary_report.get("selected_parent_id"),
            "frozen_research_hash": dictionary_report.get("frozen_research_hash"),
            "relationship_counts": dictionary_report.get("relationship_counts"),
            "requested_allocations": dictionary_report.get("requested_allocations"),
            "effective_allocations": dictionary_report.get("effective_allocations"),
        }
        if not dictionary:
            max_angles = get_workflow_angles_max_output()
            angles = _finalize_angles(
                post=post,
                entries=entry_bundle,
                angles=[],
                target=max_angles,
                raw_target=get_workflow_angles_raw_target(),
                report=report,
            )
            report.update(
                {
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                    "angles_raw_count": 0,
                    "angles_capped": False,
                    "angles_max_output": max_angles,
                    **_angle_target_report(angles, max_angles),
                }
            )
            _gen_angles_bind_log().info(
                "gen_angles_preview_complete",
                path="empty_dictionary",
                post_id=post_id,
                elapsed_ms_total=_elapsed_ms(t_preview),
                input_count=0,
                angles_count=0,
                dictionary_id=report["dictionary_id"],
            )
            return {"post": _post_with_angles(post, angles, report), "report": report}

        if generation_mode == "extractive_zero_kld":
            t_extract = time.perf_counter()
            angles = self._generate_angles_extractive(entry_bundle)
            extract_ms = _elapsed_ms(t_extract)
            angles_raw_count = len(angles)
            max_angles = get_workflow_angles_max_output()
            angles = _finalize_angles(
                post=post,
                entries=entry_bundle,
                angles=angles,
                target=max_angles,
                raw_target=get_workflow_angles_raw_target(),
                report=report,
            )
            report.update(
                {
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                    "angles_raw_count": angles_raw_count,
                    "angles_capped": angles_raw_count != len(angles),
                    "angles_max_output": max_angles,
                    **_angle_target_report(angles, max_angles),
                }
            )
            processed_post = _post_with_angles(post, angles, report)
            _gen_angles_bind_log().info(
                "gen_angles_preview_complete",
                path="extractive_zero_kld",
                post_id=post_id,
                elapsed_ms_total=_elapsed_ms(t_preview),
                elapsed_ms_extractive=extract_ms,
                input_count=len(dictionary),
                angles_count=len(angles),
                angles_hash=report["angles_hash"],
                dictionary_id=report["dictionary_id"],
                angles_raw_count=angles_raw_count,
                angles_capped=angles_raw_count != len(angles),
                angles_max_output=max_angles,
                generation_mode=generation_mode,
            )
            return {"post": processed_post, "report": report}

        if report["input_capacity_applied"]:
            _gen_angles_bind_log().warning(
                "gen_angles_dictionary_capacity_applied",
                post_id=post_id,
                dictionary_id=report["dictionary_id"],
                input_raw_count=report["input_raw_count"],
                input_count=report["input_count"],
                truncated_sources=report["input_truncated_sources"],
                input_capacity_limits=report["input_capacity_limits"],
            )

        t_an = time.perf_counter()
        try:
            response = self.backend.analyze_angles(
                dictionary,
                max_results=get_workflow_angles_raw_target(),
            )
            analyze_ms = _elapsed_ms(t_an)
            results = response.get("results", [])
            angles = []
            for result in results:
                if isinstance(result, dict):
                    angle: dict[str, Any] = {
                        "source_quote": result.get("source_quote", ""),
                        "tangent": result.get("tangent", ""),
                        "category": result.get("category", ""),
                    }
                    sd = result.get("source_document")
                    if isinstance(sd, int):
                        angle["source_document"] = sd
                    if angle["source_quote"] and angle["tangent"] and angle["category"]:
                        angles.append(angle)
            angles_raw_count = len(angles)
            max_angles = get_workflow_angles_max_output()
            angles = _finalize_angles(
                post=post,
                entries=entry_bundle,
                angles=angles,
                target=max_angles,
                raw_target=get_workflow_angles_raw_target(),
                report=report,
            )
            report.update(
                {
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                    "angles_raw_count": angles_raw_count,
                    "angles_capped": angles_raw_count != len(angles),
                    "angles_max_output": max_angles,
                    **_angle_target_report(angles, max_angles),
                }
            )
            processed_post = _post_with_angles(post, angles, report)
            _gen_angles_bind_log().info(
                "gen_angles_preview_complete",
                path="analyze_angles",
                post_id=post_id,
                elapsed_ms_total=_elapsed_ms(t_preview),
                elapsed_ms_analyze_angles=analyze_ms,
                input_count=len(dictionary),
                angles_count=len(angles),
                angles_hash=report["angles_hash"],
                dictionary_id=report["dictionary_id"],
                angles_raw_count=angles_raw_count,
                angles_capped=angles_raw_count != len(angles),
                angles_max_output=max_angles,
            )
            return {"post": processed_post, "report": report}
        except Exception as e:
            primary_ms = _elapsed_ms(t_an)
            if not allow_fallback:
                _gen_angles_bind_log().opt(exception=True).error(
                    "gen_angles_preview_failed",
                    path="analyze_angles",
                    post_id=post_id,
                    elapsed_ms_total=_elapsed_ms(t_preview),
                    elapsed_ms_primary_path=primary_ms,
                    input_count=len(dictionary),
                    error_kind=type(e).__name__,
                )
                raise
            _gen_angles_bind_log().opt(exception=True).warning(
                "gen_angles_primary_failed_using_fallback",
                post_id=post_id,
                elapsed_ms_primary_path=primary_ms,
                input_count=len(dictionary),
                error_kind=type(e).__name__,
            )
            t_fb = time.perf_counter()
            angles = self._generate_angles_llm(dictionary)
            fallback_ms = _elapsed_ms(t_fb)
            for a in angles:
                a.setdefault("source_document", 0)
            angles_raw_count = len(angles)
            max_angles = get_workflow_angles_max_output()
            angles = _finalize_angles(
                post=post,
                entries=entry_bundle,
                angles=angles,
                target=max_angles,
                raw_target=get_workflow_angles_raw_target(),
                report=report,
            )
            report.update(
                {
                    "used_fallback": True,
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                    "fallback_error": str(e),
                    "angles_raw_count": angles_raw_count,
                    "angles_capped": angles_raw_count != len(angles),
                    "angles_max_output": max_angles,
                    **_angle_target_report(angles, max_angles),
                }
            )
            processed_post = _post_with_angles(post, angles, report)
            _gen_angles_bind_log().info(
                "gen_angles_preview_complete",
                path="fallback_llm",
                post_id=post_id,
                elapsed_ms_total=_elapsed_ms(t_preview),
                elapsed_ms_primary_failed_path=primary_ms,
                elapsed_ms_fallback_llm=fallback_ms,
                input_count=len(dictionary),
                angles_count=len(angles),
                angles_hash=report["angles_hash"],
                dictionary_id=report["dictionary_id"],
                angles_raw_count=angles_raw_count,
                angles_capped=angles_raw_count != len(angles),
                angles_max_output=max_angles,
            )
            return {"post": processed_post, "report": report}

    def generate_angles(self, post: dict, allow_fallback: bool = False) -> list[dict[str, Any]]:
        """
        Generate angles from post content.

        Args:
            post: Post dictionary with content, search_results, comments

        Returns:
            List of angle dictionaries
        """
        return list(self.preview_post(post, allow_fallback=allow_fallback)["report"]["angles"])

    def _generate_angles_llm(self, texts: list[str]) -> list[dict[str, Any]]:
        """Generate angles using LLM directly."""
        combined_text = "\n\n---\n\n".join(texts)
        ga = get_prompts().gen_angles
        prompt = ga.user_template.format(combined_text=combined_text)
        system_message = ga.system_template

        try:
            provider, model = resolve_workflow_llm_provider_and_model(
                self.config.model or "mistral-nemo-instruct-2407-abliterated"
            )
            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=model,
                provider=provider,
                temperature=0.0,
            )

            parsed_items = parse_json_array_response(response)
            fallback_doc = 0
            return [
                {
                    "source_quote": a.get("source_quote", ""),
                    "tangent": a.get("tangent", ""),
                    "category": a.get("category", ""),
                    "source_document": fallback_doc,
                }
                for a in parsed_items
                if isinstance(a, dict)
            ]

        except Exception:
            _gen_angles_bind_log().opt(exception=True).error(
                "gen_angles_fallback_llm_failed",
                combined_chars=len(combined_text),
            )
            return []

    def process_post(
        self,
        post: dict,
        step: str = "angles-step",
        allow_fallback: bool = False,
    ) -> dict:
        """
        Process a post to generate angles.

        Args:
            post: Post dictionary
            step: Workflow step name

        Returns:
            Post dictionary with angles added
        """
        return self.preview_post(post, allow_fallback=allow_fallback)["post"]

    def process_posts(
        self,
        step: str = "angles-step",
        count: int = 1,
        offset: int = 0,
        tag: str | None = None,
    ) -> list[dict]:
        """
        Process multiple posts to generate angles.

        Args:
            step: Workflow step name
            count: Number of posts to process
            offset: Offset for pagination

        Returns:
            List of posts with angles added
        """
        t_batch = time.perf_counter()
        try:
            posts_list = self.backend.posts_list(step=step, count=count, offset=offset, tag=tag)
        except TypeError as exc:
            if "tag" not in str(exc):
                raise
            posts_list = self.backend.posts_list(step=step, count=count, offset=offset)
        file_names = posts_list.get("fileNames", [])
        load_failed_count = 0
        if not file_names:
            self._last_batch_summary = {
                "step": step,
                "requested_count": count,
                "listed_count": 0,
                "loaded_count": 0,
                "load_failed_count": 0,
                "processed_count": 0,
                "failed_count": 0,
            }
            _gen_angles_bind_log().info(
                "gen_angles_batch_complete",
                step=step,
                elapsed_ms_total=_elapsed_ms(t_batch),
                listed_count=0,
                loaded_count=0,
                processed_count=0,
            )
            return []

        posts: list[dict[str, Any]] = []
        for file_name in file_names:
            try:
                posts.append(self.backend.get_post_local(file_name, step))
            except Exception:
                _gen_angles_bind_log().opt(exception=True).error(
                    "gen_angles_load_failed",
                    file_name=file_name,
                    step=step,
                )
                load_failed_count += 1
        processed_posts = self.process_post_objects(posts=posts, step=step, tag=tag)
        processing_summary = dict(getattr(self, "_last_batch_summary", {}))
        processing_failed = int(processing_summary.get("failed_count", 0) or 0)
        self._last_batch_summary = {
            "step": step,
            "tag": tag,
            "requested_count": count,
            "listed_count": len(file_names),
            "loaded_count": len(posts),
            "load_failed_count": load_failed_count,
            "processed_count": len(processed_posts),
            "processing_failed_count": processing_failed,
            "failed_count": load_failed_count + processing_failed,
            "allow_fallback": bool(processing_summary.get("allow_fallback", False)),
        }
        _gen_angles_bind_log().info(
            "gen_angles_batch_complete",
            step=step,
            elapsed_ms_total=_elapsed_ms(t_batch),
            listed_count=len(file_names),
            loaded_count=len(posts),
            processed_count=len(processed_posts),
            load_failed_count=load_failed_count,
            processing_failed_count=processing_failed,
            failed_count=self._last_batch_summary["failed_count"],
        )
        if self._last_batch_summary["failed_count"] > 0:
            summ = self._last_batch_summary
            _gen_angles_bind_log().warning(
                "gen_angles_batch_degraded",
                angles_step=summ.get("step"),
                requested_count=summ.get("requested_count"),
                listed_count=summ.get("listed_count"),
                loaded_count=summ.get("loaded_count"),
                load_failed_count=summ.get("load_failed_count"),
                processed_count=summ.get("processed_count"),
                processing_failed_count=summ.get("processing_failed_count"),
                failed_count=summ.get("failed_count"),
                allow_fallback=summ.get("allow_fallback"),
            )
        return processed_posts

    def process_post_objects(
        self,
        posts: list[dict[str, Any]],
        step: str = "angles-step",
        allow_fallback: bool = False,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Process already-loaded post objects and persist angle-enriched versions."""
        processed_posts: list[dict[str, Any]] = []
        save_object = getattr(self.backend, "save_object_local", None)
        save_post = getattr(self.backend, "save_post_local", None)
        for post in posts:
            post_id = post.get("id", "<unknown>")
            t_post = time.perf_counter()
            try:
                processed = self.process_post(post, step, allow_fallback=allow_fallback)
                filename = f"{post_id}_{tag}.json" if tag else f"{post_id}.json"
                if callable(save_object):
                    save_object(processed, step=step, filename=filename)
                elif callable(save_post) and not tag:
                    save_post(processed, step=step)
                else:
                    raise AttributeError("backend is missing a compatible save method")
                processed_posts.append(processed)
                _gen_angles_bind_log().info(
                    "gen_angles_post_persisted",
                    post_id=post_id,
                    step=step,
                    tag=tag,
                    filename=filename,
                    elapsed_ms_total=_elapsed_ms(t_post),
                    options_count=processed.get("options_count"),
                )
            except Exception:
                _gen_angles_bind_log().opt(exception=True).error(
                    "gen_angles_post_failed",
                    post_id=post_id,
                    step=step,
                    elapsed_ms_until_failure=_elapsed_ms(t_post),
                )
        self._last_batch_summary = {
            "step": step,
            "tag": tag,
            "input_count": len(posts),
            "processed_count": len(processed_posts),
            "failed_count": len(posts) - len(processed_posts),
            "allow_fallback": allow_fallback,
        }
        return processed_posts

    def process_post_id(
        self,
        post_id: str,
        step: str = "angles-step",
        allow_fallback: bool = False,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """
        Process one post by ID and persist angle output.

        Args:
            post_id: Post identifier without `.json`
            step: Workflow step name

        Returns:
            Processed post dictionary with angles
        """
        file_name = f"{post_id}_{tag}.json" if tag else f"{post_id}.json"
        try:
            post = self.backend.get_post_local(file_name, step)
        except FileNotFoundError:
            post = self.backend.get_post_local(f"{post_id}.json", step)
        results = self.process_post_objects(
            posts=[post],
            step=step,
            allow_fallback=allow_fallback,
            tag=tag,
        )
        if not results:
            raise RuntimeError(f"GenAngles returned no result for post {post_id}")
        return results[0]
