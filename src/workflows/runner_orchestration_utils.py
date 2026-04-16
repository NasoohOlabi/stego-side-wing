"""Pure helpers and live-sim orchestration fragments for ``WorkflowRunner``."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from infrastructure.config import REPO_ROOT, get_env, resolve_path
from workflows.config import WorkflowConfig, isolated_workflow_config


def sum_research_preview_total_ms(entries: List[Dict[str, Any]]) -> int:
    total = 0
    for item in entries:
        rep = item.get("report")
        if not isinstance(rep, dict):
            continue
        timing = rep.get("timing")
        if not isinstance(timing, dict):
            continue
        v = timing.get("preview_total_ms")
        if isinstance(v, int):
            total += v
    return total


def research_run_with_breakdown(
    *,
    posts: List[Dict[str, Any]],
    breakdown_entries: List[Dict[str, Any]],
    batch_elapsed_ms: int,
    requested_count: int,
    offset: int,
    runner_trace_id: str,
) -> Dict[str, Any]:
    batch = {
        "elapsed_ms": batch_elapsed_ms,
        "processed_count": len(posts),
        "requested_count": requested_count,
        "offset": offset,
        "runner_trace_id": runner_trace_id,
        "preview_total_ms_sum": sum_research_preview_total_ms(breakdown_entries),
    }
    return {
        "posts": posts,
        "breakdown": {"batch": batch, "posts": breakdown_entries},
    }


def isolated_workflow_config_for_side(base: Path, side: str) -> WorkflowConfig:
    root = (base / side).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return WorkflowConfig(
        url_cache_dir=root / "url_cache",
        research_terms_db_path=root / "research_terms_cache.db",
        angles_cache_dir=root / "angles_cache",
    )


def workflow_cache_paths(cfg: WorkflowConfig) -> Dict[str, str]:
    return {
        "url_cache_dir": str(cfg.url_cache_dir),
        "research_terms_db_path": str(cfg.research_terms_db_path),
        "angles_cache_dir": str(cfg.angles_cache_dir),
    }


def double_process_cache_base_root() -> Path:
    """Shared parent for pass_1 and pass_2 dedicated cache trees."""
    raw = (get_env("DOUBLE_PROCESS_VALIDATION_ROOT") or "").strip()
    if raw:
        root = resolve_path(raw).resolve()
    else:
        root = (REPO_ROOT / "datasets" / "double_process_validation").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


_DOUBLE_PROCESS_CLAIM_NAME = "active_post_claim.json"


def double_process_claim_path() -> Path:
    return double_process_cache_base_root() / _DOUBLE_PROCESS_CLAIM_NAME


def try_read_double_process_claim() -> Optional[Tuple[str, str]]:
    """Return (post_id, file_name) if a prior run reserved a post and did not finish."""
    path = double_process_claim_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    pid = raw.get("post_id")
    if not isinstance(pid, str) or not pid.strip():
        return None
    pid = pid.strip()
    fname = raw.get("file_name")
    if not isinstance(fname, str) or not fname.strip():
        fname = f"{pid}.json"
    return pid, fname.strip()


def write_double_process_claim(post_id: str, file_name: str) -> None:
    path = double_process_claim_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        {"post_id": post_id, "file_name": file_name},
        indent=2,
        ensure_ascii=False,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def persist_double_process_final_report(dp_base: Path, body: dict[str, Any]) -> str:
    reports = dp_base / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    pid = str(body.get("post_id") or "unknown")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in pid)
    out = reports / f"{safe}_{int(time.time() * 1000)}.json"
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)


def clear_double_process_claim() -> None:
    try:
        double_process_claim_path().unlink(missing_ok=True)
    except OSError:
        pass


def is_receiver_data_load_failure(exc: Exception) -> bool:
    """True when receiver rebuild failed because URL/HTML fetch did not yield usable body."""
    return "Receiver data-load failed" in str(exc)


def compressed_full_for_live_receiver(
    stego_out: Dict[str, Any], override: Optional[str]
) -> Optional[str]:
    """Prefer explicit API override; else use sender embedding (same bitstring receiver must recover)."""
    if isinstance(override, str) and override.strip():
        return override.strip()
    emb = stego_out.get("embedding")
    if not isinstance(emb, dict):
        return None
    comp = emb.get("compression")
    if not isinstance(comp, dict):
        return None
    c = comp.get("compressed")
    return c if isinstance(c, str) and c else None


def receiver_post_from_stego(stego_result: Dict[str, Any], sender_user_id: str) -> Dict[str, Any]:
    """Attach successful stego output as a single comment from ``sender_user_id``."""
    if not stego_result.get("succeeded"):
        raise ValueError("stego did not succeed; cannot build receiver post")
    post = dict(stego_result.get("post") or {})
    stego_text = stego_result.get("stego_text")
    if not isinstance(stego_text, str) or not stego_text.strip():
        raise ValueError("stego_text missing or empty")
    pid = str(post.get("id") or "unknown")
    post["comments"] = [
        {
            "id": f"sim-stego-{pid}",
            "author": sender_user_id.strip(),
            "body": stego_text.strip(),
            "replies": [],
        }
    ]
    return post


def live_sim_attempt_root(base: Path, attempt_idx: int, multi_post: bool) -> Path:
    root = (base / f"attempt_{attempt_idx:03d}") if multi_post else base
    if multi_post:
        root.mkdir(parents=True, exist_ok=True)
    return root


def live_sim_simulation_meta(
    base: Path, attempt_idx: int, sender_cfg: WorkflowConfig, receiver_cfg: WorkflowConfig
) -> Dict[str, Any]:
    return {
        "root": str(base),
        "attempt_index": attempt_idx,
        "sender_side": str(sender_cfg.url_cache_dir.parent),
        "receiver_side": str(receiver_cfg.url_cache_dir.parent),
    }


def _live_sim_run_sender_stego(
    *,
    sender_cfg: WorkflowConfig,
    post_id: Optional[str],
    payload: Optional[str],
    tag: Optional[str],
    stego_list_offset: int,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
) -> Dict[str, Any]:
    from workflows.runner import WorkflowRunner

    with isolated_workflow_config(sender_cfg):
        sender_runner = WorkflowRunner()
        return sender_runner.run_stego(
            post_id=post_id,
            payload=payload,
            tag=tag,
            list_offset=stego_list_offset,
            on_progress=on_progress,
        )


def _live_sim_prepare_side_configs(
    base: Path, attempt_idx: int, multi_post: bool
) -> Tuple[WorkflowConfig, WorkflowConfig, Dict[str, Any]]:
    attempt_root = live_sim_attempt_root(base, attempt_idx, multi_post)
    sender_cfg = isolated_workflow_config_for_side(attempt_root, "sender")
    receiver_cfg = isolated_workflow_config_for_side(attempt_root, "receiver")
    sim_meta = live_sim_simulation_meta(base, attempt_idx, sender_cfg, receiver_cfg)
    return sender_cfg, receiver_cfg, sim_meta


def _live_sim_fail_stego(sim_meta: Dict[str, Any], stego_out: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "succeeded": False,
        "stage": "stego",
        "stego": stego_out,
        "receiver": None,
        "simulation": sim_meta,
    }


def _live_sim_fail_build_post(
    sim_meta: Dict[str, Any], exc: ValueError, stego_out: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "succeeded": False,
        "stage": "build_receiver_post",
        "error": str(exc),
        "stego": stego_out,
        "receiver": None,
        "simulation": sim_meta,
    }


def _live_sim_success_bundle(
    sim_meta: Dict[str, Any], stego_out: Dict[str, Any], recv_out: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "succeeded": True,
        "stego": stego_out,
        "receiver": recv_out,
        "simulation": sim_meta,
    }


def _live_sim_run_receiver(
    *,
    receiver_cfg: WorkflowConfig,
    recv_post: Dict[str, Any],
    uid: str,
    allow_fallback: bool,
    effective_compressed: Optional[str],
    max_padding_bits: int,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
) -> Dict[str, Any]:
    from workflows.runner import WorkflowRunner

    with isolated_workflow_config(receiver_cfg):
        rr = WorkflowRunner()
        return rr.run_receiver(
            recv_post,
            uid,
            use_fetch_cache=True,
            use_terms_cache=True,
            persist_terms_cache=True,
            use_fetch_cache_research=True,
            allow_fallback=allow_fallback,
            compressed_full=effective_compressed,
            max_padding_bits=max_padding_bits,
            on_progress=on_progress,
        )


def run_stego_receiver_live_sim_once(
    *,
    uid: str,
    post_id: Optional[str],
    stego_list_offset: int,
    payload: Optional[str],
    tag: Optional[str],
    base: Path,
    attempt_idx: int,
    multi_post: bool,
    allow_fallback: bool,
    compressed_full: Optional[str],
    max_padding_bits: int,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
) -> Dict[str, Any]:
    """Single stego + receiver pair; may raise :exc:`RuntimeError` from receiver."""
    sender_cfg, receiver_cfg, sim_meta = _live_sim_prepare_side_configs(
        base, attempt_idx, multi_post
    )
    stego_out = _live_sim_run_sender_stego(
        sender_cfg=sender_cfg,
        post_id=post_id,
        payload=payload,
        tag=tag,
        stego_list_offset=stego_list_offset,
        on_progress=on_progress,
    )
    if not stego_out.get("succeeded"):
        return _live_sim_fail_stego(sim_meta, stego_out)
    try:
        recv_post = receiver_post_from_stego(stego_out, uid)
    except ValueError as exc:
        return _live_sim_fail_build_post(sim_meta, exc, stego_out)
    eff = compressed_full_for_live_receiver(stego_out, compressed_full)
    recv_out = _live_sim_run_receiver(
        receiver_cfg=receiver_cfg,
        recv_post=recv_post,
        uid=uid,
        allow_fallback=allow_fallback,
        effective_compressed=eff,
        max_padding_bits=max_padding_bits,
        on_progress=on_progress,
    )
    return _live_sim_success_bundle(sim_meta, stego_out, recv_out)


def normalized_angles_from_raw(raw: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Same filtering as GenAnglesPipeline.preview_post for comparable hashes."""
    angles: List[Dict[str, str]] = []
    for result in raw:
        angle = {
            "source_quote": str(result.get("source_quote", "")),
            "tangent": str(result.get("tangent", "")),
            "category": str(result.get("category", "")),
        }
        if angle["source_quote"] and angle["tangent"] and angle["category"]:
            angles.append(angle)
    return angles
