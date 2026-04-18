"""Structured logging for workflow capacity: observed sizes vs effective limits."""

from __future__ import annotations

from typing import Any

from infrastructure.config import get_workflow_capacity_settings


def build_workflow_capacity_observation_fields(
    *,
    research_report: dict[str, Any] | None = None,
    gen_angles_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge effective settings with pre-cap / observed counts from pipeline reports."""
    settings = get_workflow_capacity_settings()
    out: dict[str, Any] = {
        "event": "workflow_capacity_observation",
        "capacity_profile": settings["profile"],
        "limits_enabled": settings["limits_enabled"],
        "effective_limits": {
            "research_max_terms": settings["research_max_terms"],
            "research_max_selected_urls": settings["research_max_selected_urls"],
            "dictionary_max_search_results": settings["dictionary_max_search_results"],
            "dictionary_max_comments": settings["dictionary_max_comments"],
            "angles_max_input_blocks": settings["angles_max_input_blocks"],
            "angles_max_output": settings["angles_max_output"],
        },
    }

    if research_report:
        terms_report = research_report.get("terms_report") or {}
        cap = research_report.get("capacity") or {}
        search_terms = research_report.get("search_terms") or []
        selected = research_report.get("selected_results") or []
        out["observed"] = {
            "terms_raw_count": terms_report.get("terms_raw_count"),
            "search_terms_after_cap": len(search_terms),
            "unique_urls_selected": len(selected),
            "terms_capped": bool(cap.get("terms_capped")),
            "selected_url_cap_hit": bool(cap.get("selected_url_cap_hit")),
        }

    if gen_angles_report:
        raw_sc = gen_angles_report.get("input_raw_source_counts") or {}
        obs_ga = {
            "dictionary_raw_entry_count": gen_angles_report.get("input_raw_count"),
            "dictionary_raw_source_counts": dict(raw_sc) if raw_sc else {},
            "dictionary_entry_count_after_cap": gen_angles_report.get("input_count"),
            "angles_raw_count": gen_angles_report.get("angles_raw_count"),
            "angles_count_after_cap": gen_angles_report.get("options_count"),
            "angles_capped": gen_angles_report.get("angles_capped"),
            "dictionary_capacity_applied": gen_angles_report.get("input_capacity_applied"),
        }
        if "observed" in out:
            out["observed"].update(obs_ga)
        else:
            out["observed"] = obs_ga

    return out


def log_workflow_capacity_observation(
    log: Any,
    *,
    post_id: str,
    trace_id: str,
    research_report: dict[str, Any] | None = None,
    gen_angles_report: dict[str, Any] | None = None,
) -> None:
    """Emit one structured INFO line with observed sizes and effective limits."""
    fields = build_workflow_capacity_observation_fields(
        research_report=research_report,
        gen_angles_report=gen_angles_report,
    )
    log.info(
        "workflow_capacity_observation",
        post_id=post_id,
        trace_id=trace_id,
        **fields,
    )
