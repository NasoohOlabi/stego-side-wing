"""Workflow runner for orchestrating pipeline execution."""
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from infrastructure.config import REPO_ROOT, get_env, resolve_path
from infrastructure.json_logging import get_trace_id
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.config import WorkflowConfig, isolated_workflow_config
from workflows.pipelines.data_load import DataLoadPipeline
from workflows.pipelines.decode import DecodePipeline
from workflows.pipelines.gen_angles import GenAnglesPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline
from workflows.pipelines.receiver import ReceiverPipeline
from workflows.pipelines.research import ResearchPipeline, is_likely_google_quota_error
from workflows.pipelines.stego import StegoPipeline
from workflows.utils.debug_probe import write_debug_probe
from workflows.utils.protocol_utils import stable_hash
from services.workflow_run_tracker import get_run_id

_LOG = logger.bind(component="WorkflowRunner")


def _sum_research_preview_total_ms(entries: List[Dict[str, Any]]) -> int:
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


def _research_run_with_breakdown(
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
        "preview_total_ms_sum": _sum_research_preview_total_ms(breakdown_entries),
    }
    return {
        "posts": posts,
        "breakdown": {"batch": batch, "posts": breakdown_entries},
    }


def _isolated_workflow_config_for_side(base: Path, side: str) -> WorkflowConfig:
    root = (base / side).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return WorkflowConfig(
        url_cache_dir=root / "url_cache",
        research_terms_db_path=root / "research_terms_cache.db",
        angles_cache_dir=root / "angles_cache",
    )


def _workflow_cache_paths(cfg: WorkflowConfig) -> Dict[str, str]:
    return {
        "url_cache_dir": str(cfg.url_cache_dir),
        "research_terms_db_path": str(cfg.research_terms_db_path),
        "angles_cache_dir": str(cfg.angles_cache_dir),
    }


def _double_process_validation_workflow_config() -> WorkflowConfig:
    raw = (get_env("DOUBLE_PROCESS_VALIDATION_ROOT") or "").strip()
    if raw:
        root = resolve_path(raw).resolve()
    else:
        root = (REPO_ROOT / "datasets" / "double_process_validation").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return WorkflowConfig(
        url_cache_dir=root / "url_cache",
        research_terms_db_path=root / "research_terms_cache.db",
        angles_cache_dir=root / "angles_cache",
    )


def _is_receiver_data_load_failure(exc: Exception) -> bool:
    """True when receiver rebuild failed because URL/HTML fetch did not yield usable body."""
    return "Receiver data-load failed" in str(exc)


def _compressed_full_for_live_receiver(
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


def _receiver_post_from_stego(stego_result: Dict[str, Any], sender_user_id: str) -> Dict[str, Any]:
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


def _live_sim_attempt_root(base: Path, attempt_idx: int, multi_post: bool) -> Path:
    root = (base / f"attempt_{attempt_idx:03d}") if multi_post else base
    if multi_post:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _live_sim_simulation_meta(
    base: Path, attempt_idx: int, sender_cfg: WorkflowConfig, receiver_cfg: WorkflowConfig
) -> Dict[str, Any]:
    return {
        "root": str(base),
        "attempt_index": attempt_idx,
        "sender_side": str(sender_cfg.url_cache_dir.parent),
        "receiver_side": str(receiver_cfg.url_cache_dir.parent),
    }


def _run_stego_receiver_live_sim_once(
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
    attempt_root = _live_sim_attempt_root(base, attempt_idx, multi_post)
    sender_cfg = _isolated_workflow_config_for_side(attempt_root, "sender")
    receiver_cfg = _isolated_workflow_config_for_side(attempt_root, "receiver")
    sim_meta = _live_sim_simulation_meta(base, attempt_idx, sender_cfg, receiver_cfg)

    with isolated_workflow_config(sender_cfg):
        sender_runner = WorkflowRunner()
        stego_out = sender_runner.run_stego(
            post_id=post_id,
            payload=payload,
            tag=tag,
            list_offset=stego_list_offset,
            on_progress=on_progress,
        )

    if not stego_out.get("succeeded"):
        return {
            "succeeded": False,
            "stage": "stego",
            "stego": stego_out,
            "receiver": None,
            "simulation": sim_meta,
        }

    try:
        recv_post = _receiver_post_from_stego(stego_out, uid)
    except ValueError as exc:
        return {
            "succeeded": False,
            "stage": "build_receiver_post",
            "error": str(exc),
            "stego": stego_out,
            "receiver": None,
            "simulation": sim_meta,
        }

    effective_compressed = _compressed_full_for_live_receiver(stego_out, compressed_full)

    with isolated_workflow_config(receiver_cfg):
        rr = WorkflowRunner()
        recv_out = rr.run_receiver(
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

    return {
        "succeeded": True,
        "stego": stego_out,
        "receiver": recv_out,
        "simulation": sim_meta,
    }


def _normalized_angles_from_raw(raw: List[Dict[str, Any]]) -> List[Dict[str, str]]:
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


class WorkflowRunner:
    """Owns pipeline instances, fetch-failure counters, and orchestration entry points.

    Logs use module ``_LOG`` so instances created via ``__new__`` (tests) still emit
    with component ``WorkflowRunner`` without running ``__init__``.
    """

    def __init__(self) -> None:
        self.backend = BackendAPIAdapter()
        self.data_load = DataLoadPipeline()
        self.research = ResearchPipeline()
        self.gen_angles = GenAnglesPipeline()
        self.stego = StegoPipeline()
        self.decode = DecodePipeline()
        self.receiver = ReceiverPipeline()
        self.gen_terms = GenSearchTermsPipeline()
        # In-memory counters for data-load URL fetch failures by post id.
        # This resets when the API process restarts.
        self._fetch_fail_counts: Dict[str, int] = {}

    @staticmethod
    def _emit(
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
        event: str,
        payload: Dict[str, Any],
    ) -> None:
        if on_progress is None:
            return
        try:
            on_progress(event, payload)
        except Exception:
            # Progress reporting must never break workflow execution.
            return

    @staticmethod
    def _call_with_optional_progress(
        func: Callable[..., Any],
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
        **kwargs: Any,
    ) -> Any:
        if on_progress is None:
            return func(**kwargs)
        try:
            return func(on_progress=on_progress, **kwargs)
        except TypeError as exc:
            # Tests may monkeypatch runner methods with simple lambdas.
            if "on_progress" not in str(exc):
                raise
            return func(**kwargs)

    def _artifact_path(self, step: str, post_id: str) -> Path:
        _, dest_dir = self.backend.config.get_step_dirs(step)
        return dest_dir / f"{post_id}.json"

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _summarize_stage_payload(stage_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"hash": stable_hash(payload)}
        if stage_name == "data_load":
            selftext = payload.get("selftext", "")
            summary.update(
                {
                    "selftext_hash": stable_hash(selftext),
                    "selftext_length": len(selftext) if isinstance(selftext, str) else 0,
                }
            )
            return summary
        if stage_name == "research":
            results = payload.get("search_results", [])
            summary.update(
                {
                    "search_results_hash": stable_hash(results),
                    "search_results_count": len(results) if isinstance(results, list) else 0,
                }
            )
            return summary
        if stage_name == "gen_angles":
            angles = payload.get("angles", [])
            summary.update(
                {
                    "angles_hash": stable_hash(angles),
                    "angles_count": len(angles) if isinstance(angles, list) else 0,
                    "options_count": payload.get("options_count"),
                }
            )
            return summary
        return summary

    def preview_data_load_post(
        self,
        post_id: str,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        return self.data_load.preview_post_id(
            post_id=post_id,
            step="filter-url-unresolved",
            use_cache=use_cache,
        )

    def preview_research_post(
        self,
        post_id: str,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        source_post: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        file_name = f"{post_id}.json"
        post = source_post or self.backend.get_post_local(file_name, "filter-researched")
        return self.research.preview_post(
            post=post,
            step="filter-researched",
            force=True,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )

    def preview_gen_angles_post(
        self,
        post_id: str,
        source_post: Optional[Dict[str, Any]] = None,
        allow_fallback: bool = False,
    ) -> Dict[str, Any]:
        file_name = f"{post_id}.json"
        post = source_post or self.backend.get_post_local(file_name, "angles-step")
        return self.gen_angles.preview_post(post=post, allow_fallback=allow_fallback)

    @classmethod
    def _collect_diff_paths(
        cls,
        left: Any,
        right: Any,
        prefix: str = "",
        limit: int = 50,
    ) -> List[str]:
        diffs: List[str] = []

        def walk(a: Any, b: Any, path: str) -> None:
            if len(diffs) >= limit:
                return
            if type(a) is not type(b):
                diffs.append(path or "$")
                return
            if isinstance(a, dict):
                a_keys = set(a.keys())
                b_keys = set(b.keys())
                for key in sorted(a_keys - b_keys):
                    if len(diffs) >= limit:
                        return
                    next_path = f"{path}.{key}" if path else key
                    diffs.append(next_path)
                for key in sorted(b_keys - a_keys):
                    if len(diffs) >= limit:
                        return
                    next_path = f"{path}.{key}" if path else key
                    diffs.append(next_path)
                for key in sorted(a_keys & b_keys):
                    next_path = f"{path}.{key}" if path else key
                    walk(a[key], b[key], next_path)
                return
            if isinstance(a, list):
                if len(a) != len(b):
                    diffs.append(path or "$")
                    return
                for idx, (a_item, b_item) in enumerate(zip(a, b)):
                    next_path = f"{path}[{idx}]" if path else f"[{idx}]"
                    walk(a_item, b_item, next_path)
                return
            if a != b:
                diffs.append(path or "$")

        walk(left, right, prefix)
        return diffs
    
    def run_data_load(
        self,
        count: int = 100,
        offset: int = 0,
        batch_size: int = 5,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict]:
        """Run DataLoad pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "data-load", "count": count, "offset": offset, "batch_size": batch_size},
        )
        t0 = time.perf_counter()
        results = self.data_load.process_posts(
            step="filter-url-unresolved",
            count=count,
            offset=offset,
            batch_size=batch_size,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _LOG.bind(trace_id=get_trace_id()).info(
            "workflow_data_load_complete",
            elapsed_ms=elapsed_ms,
            processed_count=len(results),
            count=count,
            offset=offset,
            batch_size=batch_size,
        )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "data-load",
                "processed_count": len(results),
                "elapsed_ms": elapsed_ms,
            },
        )
        return results
    
    def run_research(
        self,
        count: int = 1,
        offset: int = 0,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        include_breakdown: bool = False,
    ) -> Any:
        """Run Research pipeline."""
        trace_id = str(uuid4())
        rid = _LOG.bind(trace_id=trace_id)
        t0 = time.perf_counter()
        rid.info(
            "workflow_research_run_begin",
            event="research_timing",
            count=count,
            offset=offset,
            include_breakdown=include_breakdown,
        )
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "research", "count": count, "offset": offset},
        )
        results = self.research.process_posts(
            step="filter-researched",
            count=count,
            offset=offset,
            include_breakdown=include_breakdown,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        processed_count = len(results)
        rid.info(
            "workflow_research_run_complete",
            event="research_timing",
            elapsed_ms=elapsed_ms,
            processed_count=processed_count,
            include_breakdown=include_breakdown,
        )
        payload_out: Any = results
        if include_breakdown:
            entries = list(self.research.last_research_breakdown_posts)
            payload_out = _research_run_with_breakdown(
                posts=results,
                breakdown_entries=entries,
                batch_elapsed_ms=elapsed_ms,
                requested_count=count,
                offset=offset,
                runner_trace_id=trace_id,
            )
            rid.info(
                "research_breakdown_batch",
                event="research_breakdown_batch",
                batch_elapsed_ms=elapsed_ms,
                processed_count=processed_count,
                preview_total_ms_sum=payload_out["breakdown"]["batch"]["preview_total_ms_sum"],
                requested_count=count,
                offset=offset,
            )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "research",
                "processed_count": processed_count,
                "elapsed_ms": elapsed_ms,
            },
        )
        return payload_out
    
    def run_gen_angles(
        self,
        count: int = 1,
        offset: int = 0,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict]:
        """Run GenAngles pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "gen-angles", "count": count, "offset": offset},
        )
        t0 = time.perf_counter()
        results = self.gen_angles.process_posts(
            step="angles-step",
            count=count,
            offset=offset,
        )
        batch_ms = int((time.perf_counter() - t0) * 1000)
        summary = dict(getattr(self.gen_angles, "_last_batch_summary", {}) or {})
        tid = get_trace_id()
        _LOG.bind(trace_id=tid if tid else "").info(
            "workflow_gen_angles_batch_timing",
            elapsed_ms=batch_ms,
            processed_count=len(results),
            requested_count=summary.get("requested_count"),
            listed_count=summary.get("listed_count"),
            loaded_count=summary.get("loaded_count"),
            load_failed_count=summary.get("load_failed_count"),
            processing_failed_count=summary.get("processing_failed_count"),
            failed_count=summary.get("failed_count"),
            degraded=bool(summary.get("failed_count", 0)),
        )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "gen-angles",
                "processed_count": len(results),
                "elapsed_ms": batch_ms,
                "requested_count": summary.get("requested_count"),
                "listed_count": summary.get("listed_count"),
                "loaded_count": summary.get("loaded_count"),
                "load_failed_count": summary.get("load_failed_count"),
                "processing_failed_count": summary.get("processing_failed_count"),
                "failed_count": summary.get("failed_count"),
                "degraded": bool(summary.get("failed_count", 0)),
            },
        )
        return results
    
    def run_stego(
        self,
        post_id: Optional[str] = None,
        payload: Optional[str] = None,
        tag: Optional[str] = None,
        list_offset: int = 1,
        run_all: bool = False,
        max_posts: Optional[int] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run Stego pipeline."""
        max_posts_cap: Optional[int] = (
            max_posts if max_posts is not None and max_posts >= 1 else None
        )
        if run_all and post_id:
            raise ValueError("'post_id' cannot be combined with run_all=true")

        self._emit(
            on_progress,
            "stage_start",
            {
                "stage": "stego",
                "post_id": post_id,
                "tag": tag,
                "list_offset": list_offset,
                "run_all": run_all,
                "max_posts": max_posts_cap,
            },
        )
        if not run_all:
            t0 = time.perf_counter()
            result = self.stego.process_post(
                post_id=post_id,
                payload=payload,
                tag=tag,
                list_offset=list_offset,
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._emit(
                on_progress,
                "stage_done",
                {
                    "stage": "stego",
                    "succeeded": bool(result.get("succeeded")),
                    "retry_count": int(result.get("retry_count", 0)),
                    "elapsed_ms": elapsed_ms,
                },
            )
            return result

        results: List[Dict[str, Any]] = []
        success_count = 0
        failure_count = 0
        seen_failed_post_ids: set[str] = set()
        stop_reason = "no_unprocessed_posts"
        t_run_all = time.perf_counter()

        while True:
            if max_posts_cap is not None and len(results) >= max_posts_cap:
                stop_reason = "max_posts_reached"
                break
            try:
                result = self.stego.process_post(
                    post_id=None,
                    payload=payload,
                    tag=tag,
                    list_offset=list_offset,
                )
            except ValueError as exc:
                if "No unprocessed posts found" in str(exc):
                    stop_reason = "no_unprocessed_posts"
                    break
                raise

            results.append(result)
            succeeded = bool(result.get("succeeded"))
            post_obj = result.get("post")
            post_id_value = (
                str(post_obj.get("id"))
                if isinstance(post_obj, dict) and post_obj.get("id") is not None
                else None
            )
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "stego",
                    "run_all": True,
                    "processed_count": len(results),
                    "post_id": post_id_value,
                    "succeeded": succeeded,
                    "retry_count": int(result.get("retry_count", 0)),
                },
            )

            if succeeded:
                success_count += 1
                continue

            failure_count += 1
            if not post_id_value:
                stop_reason = "failed_post_without_id"
                break
            if post_id_value in seen_failed_post_ids:
                stop_reason = "repeat_failed_post"
                break
            seen_failed_post_ids.add(post_id_value)

        result = {
            "run_all": True,
            "tag": tag,
            "list_offset": list_offset,
            "max_posts": max_posts_cap,
            "processed_count": len(results),
            "succeeded_count": success_count,
            "failed_count": failure_count,
            "stopped_reason": stop_reason,
            "results": results,
        }
        run_all_elapsed_ms = int((time.perf_counter() - t_run_all) * 1000)
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "stego",
                "run_all": True,
                "processed_count": len(results),
                "succeeded_count": success_count,
                "failed_count": failure_count,
                "stopped_reason": stop_reason,
                "elapsed_ms": run_all_elapsed_ms,
            },
        )
        return result
    
    def run_decode(
        self,
        stego_text: str,
        angles: List[Dict[str, Any]],
        few_shots: Optional[List[Dict[str, Any]]] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Optional[int]:
        """Run Decode pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "decode", "angles_count": len(angles)},
        )
        decoded_idx = self.decode.decode(
            stego_text=stego_text,
            angles=angles,
            few_shots=few_shots,
        )
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "decode", "decoded_index": decoded_idx},
        )
        return decoded_idx

    def run_receiver(
        self,
        post: Dict[str, Any],
        sender_user_id: str,
        *,
        use_fetch_cache: bool = True,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache_research: bool = True,
        allow_fallback: bool = False,
        compressed_full: Optional[str] = None,
        max_padding_bits: int = 256,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Rebuild context on the receiver and recover the stego payload."""
        self._emit(
            on_progress,
            "stage_start",
            {
                "stage": "receiver",
                "post_id": post.get("id"),
                "sender_user_id": sender_user_id,
            },
        )
        try:
            result = self.receiver.run(
                post,
                sender_user_id,
                use_fetch_cache=use_fetch_cache,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache_research=use_fetch_cache_research,
                allow_fallback=allow_fallback,
                compressed_full=compressed_full,
                max_padding_bits=max_padding_bits,
                on_progress=on_progress,
            )
        except Exception:
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "receiver", "succeeded": False},
            )
            raise
        self._emit(
            on_progress,
            "stage_done",
            {"stage": "receiver", "succeeded": True, "post_id": post.get("id")},
        )
        return result

    def run_stego_receiver_live_sim(
        self,
        sender_user_id: str,
        *,
        post_id: Optional[str] = None,
        payload: Optional[str] = None,
        tag: Optional[str] = None,
        list_offset: int = 1,
        simulation_root: Optional[Path] = None,
        max_post_attempts: int = 25,
        allow_fallback: bool = False,
        compressed_full: Optional[str] = None,
        max_padding_bits: int = 256,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run stego then receiver with disjoint on-disk caches (cold receiver).

        When ``post_id`` is omitted, advances ``list_offset`` on receiver data-load
        HTML failures (and on stego failure) up to ``max_post_attempts`` tries.
        """
        uid = sender_user_id.strip()
        if not uid:
            raise ValueError("'sender_user_id' must be non-empty")

        base = simulation_root or Path(tempfile.mkdtemp(prefix=f"live_sim_{uuid4().hex}_"))
        base = base.resolve()
        multi = post_id is None
        attempts = max(1, max_post_attempts) if multi else 1
        skipped: List[Dict[str, Any]] = []

        for attempt_idx in range(attempts):
            stego_off = list_offset + attempt_idx
            try:
                one = _run_stego_receiver_live_sim_once(
                    uid=uid,
                    post_id=post_id,
                    stego_list_offset=stego_off if multi else list_offset,
                    payload=payload,
                    tag=tag,
                    base=base,
                    attempt_idx=attempt_idx,
                    multi_post=multi,
                    allow_fallback=allow_fallback,
                    compressed_full=compressed_full,
                    max_padding_bits=max_padding_bits,
                    on_progress=on_progress,
                )
            except Exception as exc:
                if multi and (
                    _is_receiver_data_load_failure(exc)
                    or is_likely_google_quota_error(exc)
                ):
                    stage = (
                        "receiver_data_load"
                        if _is_receiver_data_load_failure(exc)
                        else "search_quota"
                    )
                    _LOG.info(
                        "live_sim_skip_post stage={} attempt={} offset={} err={}",
                        stage,
                        attempt_idx,
                        stego_off,
                        str(exc)[:200],
                    )
                    skipped.append(
                        {
                            "stage": stage,
                            "list_offset": stego_off,
                            "error": str(exc),
                        }
                    )
                    continue
                raise

            one["skipped_posts"] = list(skipped)
            if one.get("succeeded"):
                return one

            if multi:
                skipped.append(
                    {
                        "stage": "stego",
                        "list_offset": stego_off,
                        "stego": one.get("stego"),
                    }
                )
                continue

            return one

        return {
            "succeeded": False,
            "stage": "exhausted_attempts",
            "error": "No post succeeded within max_post_attempts",
            "stego": None,
            "receiver": None,
            "simulation": {"root": str(base)},
            "skipped_posts": skipped,
        }

    def run_gen_search_terms(
        self,
        post_id: str,
        post_title: Optional[str] = None,
        post_text: Optional[str] = None,
        post_url: Optional[str] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[str]:
        """Run GenSearchTerms pipeline."""
        self._emit(
            on_progress,
            "stage_start",
            {"stage": "gen-terms", "post_id": post_id},
        )
        report = self.gen_terms.preview_generation(
            post_id=post_id,
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
        )
        terms = list(report.get("terms", []))
        stage_done = {
            "stage": "gen-terms",
            "terms_count": len(terms),
            "terms_hash": report.get("terms_hash"),
            "used_cache": report.get("used_cache"),
            "cache_hit": report.get("cache_hit"),
            "retry_count": report.get("retry_count"),
            "elapsed_ms": report.get("elapsed_ms"),
            "parse_mode": report.get("parse_mode"),
            "degraded": bool(report.get("error")),
        }
        if report.get("error"):
            stage_done["error"] = report.get("error")
            stage_done["error_kind"] = report.get("error_kind")
            stage_done["http_status"] = report.get("http_status")
        self._emit(
            on_progress,
            "stage_done",
            stage_done,
        )
        return terms

    def validate_post_pipeline(
        self,
        post_id: str,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        use_terms_cache: bool = False,
        persist_terms_cache: bool = False,
        use_fetch_cache: bool = False,
        allow_angles_fallback: bool = False,
    ) -> Dict[str, Any]:
        """
        Replay the live protocol for one post and compare with saved stage artifacts.

        Args:
            post_id: Post identifier without `.json`
            on_progress: Optional progress callback

        Returns:
            Validation report with per-stage strict equality results.
        """
        if not post_id or not post_id.strip():
            raise ValueError("'post_id' must be a non-empty string")
        post_id = post_id.strip()

        stage_steps = {
            "data_load": "filter-url-unresolved",
            "research": "filter-researched",
            "gen_angles": "angles-step",
        }

        baseline: Dict[str, Dict[str, Any]] = {}
        for stage_name, step in stage_steps.items():
            path = self._artifact_path(step, post_id)
            if not path.exists():
                raise FileNotFoundError(
                    f"Baseline artifact missing for stage '{stage_name}': {path}"
                )
            baseline[stage_name] = self._load_json(path)

        self._emit(
            on_progress,
            "stage_start",
            {"stage": "validate-post", "post_id": post_id},
        )
        stage_errors: Dict[str, str] = {}
        rerun_payloads: Dict[str, Dict[str, Any]] = {}
        protocol_reports: Dict[str, Dict[str, Any]] = {}

        self._emit(
            on_progress,
            "stage_progress",
            {"stage": "validate-post", "event": "rerun_data_load", "post_id": post_id},
        )
        try:
            data_load_preview = self.preview_data_load_post(
                post_id=post_id,
                use_cache=use_fetch_cache,
            )
            rerun_payloads["data_load"] = data_load_preview["post"]
            protocol_reports["data_load"] = data_load_preview["report"]
            if not protocol_reports["data_load"].get("fetch_success"):
                stage_errors["data_load"] = str(
                    protocol_reports["data_load"].get("error") or "data-load fetch failed"
                )
        except Exception as exc:
            stage_errors["data_load"] = str(exc)

        if "data_load" not in stage_errors:
            self._emit(
                on_progress,
                "stage_progress",
                {"stage": "validate-post", "event": "rerun_research", "post_id": post_id},
            )
            try:
                research_preview = self.preview_research_post(
                    post_id=post_id,
                    source_post=rerun_payloads["data_load"],
                    use_terms_cache=use_terms_cache,
                    persist_terms_cache=persist_terms_cache,
                    use_fetch_cache=use_fetch_cache,
                )
                rerun_payloads["research"] = research_preview["post"]
                protocol_reports["research"] = research_preview["report"]
                if protocol_reports["research"].get("error"):
                    stage_errors["research"] = str(protocol_reports["research"]["error"])
            except Exception as exc:
                stage_errors["research"] = str(exc)

        if "data_load" not in stage_errors and "research" not in stage_errors:
            self._emit(
                on_progress,
                "stage_progress",
                {"stage": "validate-post", "event": "rerun_gen_angles", "post_id": post_id},
            )
            try:
                angles_preview = self.preview_gen_angles_post(
                    post_id=post_id,
                    source_post=rerun_payloads["research"],
                    allow_fallback=allow_angles_fallback,
                )
                rerun_payloads["gen_angles"] = angles_preview["post"]
                protocol_reports["gen_angles"] = angles_preview["report"]
            except Exception as exc:
                stage_errors["gen_angles"] = str(exc)

        steps_report: Dict[str, Dict[str, Any]] = {}
        valid = True
        upstream_failed = False
        for stage_name, step in stage_steps.items():
            if upstream_failed:
                steps_report[stage_name] = {
                    "step": step,
                    "comparison": "skipped",
                    "matches": None,
                    "changed_keys": [],
                    "comparison_note": (
                        "Not compared: a previous stage failed during rerun, so this stage was skipped. "
                        "This is not a baseline-vs-rerun mismatch."
                    ),
                    "error": "Skipped because an upstream stage failed during rerun",
                }
                valid = False
                continue

            if stage_name in stage_errors:
                steps_report[stage_name] = {
                    "step": step,
                    "comparison": "rerun_failed",
                    "matches": None,
                    "changed_keys": [],
                    "comparison_note": (
                        "Live rerun did not finish successfully, so the saved artifact was not compared "
                        "to a fresh rerun. Treat this as an execution/network/provider failure, not a "
                        "protocol drift mismatch."
                    ),
                    "error": stage_errors[stage_name],
                    "baseline_summary": self._summarize_stage_payload(
                        stage_name, baseline[stage_name]
                    ),
                    "protocol_report": protocol_reports.get(stage_name),
                }
                valid = False
                upstream_failed = True
                continue

            rerun_payload = rerun_payloads[stage_name]
            baseline_payload = baseline[stage_name]
            matches = baseline_payload == rerun_payload
            changed_keys = [] if matches else self._collect_diff_paths(
                baseline_payload, rerun_payload
            )
            steps_report[stage_name] = {
                "step": step,
                "comparison": "match" if matches else "mismatch",
                "matches": matches,
                "changed_keys": changed_keys,
                "comparison_note": (
                    "Saved artifact and live rerun are byte-for-byte equal."
                    if matches
                    else (
                        "Mismatch: live rerun produced different JSON than the saved workflow artifact "
                        "for this stage (see changed_keys). This indicates protocol or data drift, not "
                        "a failed rerun."
                    )
                ),
                "baseline_summary": self._summarize_stage_payload(stage_name, baseline_payload),
                "rerun_summary": self._summarize_stage_payload(stage_name, rerun_payload),
                "protocol_report": protocol_reports.get(stage_name),
            }
            valid = valid and matches

        outcome: str
        if valid:
            outcome = "protocol_match"
            validation_explanation = (
                "All three stages completed and each live rerun matched its saved artifact."
            )
        elif any(
            steps_report[s].get("comparison") == "mismatch" for s in stage_steps
        ):
            outcome = "protocol_mismatch"
            validation_explanation = (
                "At least one stage finished rerunning but the live payload differed from the saved "
                "artifact. That is a true baseline-vs-rerun mismatch (see comparison / changed_keys on "
                "those stages)."
            )
        else:
            outcome = "rerun_incomplete"
            validation_explanation = (
                "A stage failed during rerun or was skipped, so validation could not establish whether "
                "the protocol still matches baselines. This is not labeled as a protocol mismatch; "
                "fix the failing stage and retry."
            )

        result = {
            "post_id": post_id,
            "valid": valid,
            "validation_outcome": outcome,
            "validation_explanation": validation_explanation,
            "mode": "live_protocol_replay",
            "settings": {
                "use_terms_cache": use_terms_cache,
                "persist_terms_cache": persist_terms_cache,
                "use_fetch_cache": use_fetch_cache,
                "allow_angles_fallback": allow_angles_fallback,
            },
            "steps": steps_report,
        }
        _LOG.info(
            "validate_post post_id={} valid={} use_terms_cache={} use_fetch_cache={}",
            post_id,
            valid,
            use_terms_cache,
            use_fetch_cache,
        )
        self._emit(
            on_progress,
            "stage_done",
            {
                "stage": "validate-post",
                "post_id": post_id,
                "valid": valid,
            },
        )
        return result

    def _select_next_new_post(self, offset: int = 0) -> tuple[str, str]:
        """
        Pick one new post from filter-url-unresolved source queue.

        Returns:
            Tuple of (post_id, file_name)
        """
        listing = self.backend.posts_list(step="filter-url-unresolved", count=1, offset=offset)
        file_names = listing.get("fileNames", [])
        if not file_names:
            raise ValueError(
                "No new posts available in datasets/news_cleaned that are not in datasets/news_url_fetched."
            )
        file_name = str(file_names[0])
        post_id = Path(file_name).stem
        if not post_id:
            raise ValueError(f"Invalid post filename returned by posts_list: {file_name!r}")
        return post_id, file_name

    @staticmethod
    def _is_data_load_fetch_failure(exc: Exception) -> bool:
        message = str(exc)
        return "Failed to fetch URL content for post" in message

    def _record_fetch_failure(self, post_id: str) -> int:
        if not hasattr(self, "_fetch_fail_counts"):
            self._fetch_fail_counts = {}
        next_count = int(self._fetch_fail_counts.get(post_id, 0)) + 1
        self._fetch_fail_counts[post_id] = next_count
        return next_count

    def _clear_fetch_failure(self, post_id: str) -> None:
        if not hasattr(self, "_fetch_fail_counts"):
            self._fetch_fail_counts = {}
        self._fetch_fail_counts.pop(post_id, None)

    @staticmethod
    def _slim_substage_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
        keys = (
            "hash",
            "selftext_length",
            "search_results_count",
            "angles_count",
            "options_count",
        )
        return {k: summary[k] for k in keys if k in summary}

    def _double_process_substage_begin(
        self,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
    ) -> None:
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "substage_begin",
                "post_id": post_id,
                "pass": pass_num,
                "cache_mode": cache_mode,
                "pipeline_substage": pipeline_substage,
            },
        )
        _LOG.bind(
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
        ).info("workflow_progress_double_process_substage_begin")

    def _double_process_substage_end(
        self,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
        elapsed_ms: int,
        summary: Dict[str, Any],
    ) -> None:
        slim = self._slim_substage_summary(summary)
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "substage_end",
                "post_id": post_id,
                "pass": pass_num,
                "cache_mode": cache_mode,
                "pipeline_substage": pipeline_substage,
                "elapsed_ms": elapsed_ms,
                "summary": slim,
            },
        )
        _LOG.bind(
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
            elapsed_ms=elapsed_ms,
            step_hash=summary.get("hash"),
        ).info("workflow_progress_double_process_substage_end")

    def _double_process_substage_failed(
        self,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
        elapsed_ms: int,
        exc: BaseException,
    ) -> None:
        err_text = str(exc)
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "substage_failed",
                "post_id": post_id,
                "pass": pass_num,
                "cache_mode": cache_mode,
                "pipeline_substage": pipeline_substage,
                "elapsed_ms": elapsed_ms,
                "error": err_text[:2000],
            },
        )
        _LOG.bind(
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
            elapsed_ms=elapsed_ms,
        ).opt(exception=exc).error("workflow_progress_double_process_substage_failed")

    def _run_timed_dp_substage(
        self,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
        *,
        post_id: str,
        pass_num: int,
        cache_mode: str,
        pipeline_substage: str,
        run_fn: Callable[[], Dict[str, Any]],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        self._double_process_substage_begin(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
        )
        t0 = time.perf_counter()
        try:
            raw = run_fn()
        except BaseException as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            self._double_process_substage_failed(
                on_progress,
                post_id=post_id,
                pass_num=pass_num,
                cache_mode=cache_mode,
                pipeline_substage=pipeline_substage,
                elapsed_ms=elapsed_ms,
                exc=exc,
            )
            raise
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        summary = self._summarize_stage_payload(pipeline_substage, raw)
        self._double_process_substage_end(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage=pipeline_substage,
            elapsed_ms=elapsed_ms,
            summary=summary,
        )
        return raw, summary

    def _run_three_stage_post(
        self,
        post_id: str,
        *,
        use_terms_cache: bool,
        persist_terms_cache: bool,
        use_fetch_cache: bool,
        allow_angles_fallback: bool,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        pass_num: int = 1,
        cache_mode: str = "unknown",
    ) -> Dict[str, Any]:
        """Run data_load -> research -> gen_angles for one post ID."""
        _, data_summary = self._run_timed_dp_substage(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage="data_load",
            run_fn=lambda: self.data_load.process_post_id(
                post_id=post_id,
                step="filter-url-unresolved",
                use_cache=use_fetch_cache,
            ),
        )
        _, research_summary = self._run_timed_dp_substage(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage="research",
            run_fn=lambda: self.research.process_post_id(
                post_id=post_id,
                step="filter-researched",
                force=True,
                use_terms_cache=use_terms_cache,
                persist_terms_cache=persist_terms_cache,
                use_fetch_cache=use_fetch_cache,
            ),
        )
        _, angles_summary = self._run_timed_dp_substage(
            on_progress,
            post_id=post_id,
            pass_num=pass_num,
            cache_mode=cache_mode,
            pipeline_substage="gen_angles",
            run_fn=lambda: self.gen_angles.process_post_id(
                post_id=post_id,
                step="angles-step",
                allow_fallback=allow_angles_fallback,
            ),
        )
        return {
            "settings": {
                "use_terms_cache": use_terms_cache,
                "persist_terms_cache": persist_terms_cache,
                "use_fetch_cache": use_fetch_cache,
                "allow_angles_fallback": allow_angles_fallback,
            },
            "steps": {
                "data_load": data_summary,
                "research": research_summary,
                "gen_angles": angles_summary,
            },
        }

    def run_double_process_new_post(
        self,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        allow_angles_fallback: bool = False,
    ) -> Dict[str, Any]:
        """
        Process one new post twice through data_load -> research -> gen_angles.

        Pass 1 uses the main URL/terms/angles caches. Pass 2 uses the same cache
        flags but binds an isolated validation cache namespace (see
        ``DOUBLE_PROCESS_VALIDATION_ROOT``).
        """
        self._emit(
            on_progress,
            "workflow_start",
            {
                "workflow": "double-process-new-post",
                "allow_angles_fallback": allow_angles_fallback,
            },
        )
        # region agent log
        write_debug_probe(
            run_id=str(get_run_id() or ""),
            hypothesis_id="H4",
            location="workflows/runner.py:run_double_process_new_post:start",
            message="double-process workflow started",
            data={"allow_angles_fallback": allow_angles_fallback},
        )
        # endregion
        post_id, file_name = self._select_next_new_post()
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "selected_post",
                "post_id": post_id,
                "file_name": file_name,
                "offset": 0,
            },
        )
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "pass_1_cached_start",
                "post_id": post_id,
            },
        )
        # region agent log
        write_debug_probe(
            run_id=str(get_run_id() or ""),
            hypothesis_id="H4",
            location="workflows/runner.py:run_double_process_new_post:pass1_start",
            message="double-process pass 1 started",
            data={"post_id": post_id, "cache_mode": "main"},
        )
        # endregion
        t_pass1 = time.perf_counter()
        while True:
            try:
                first_pass = self._run_three_stage_post(
                    post_id=post_id,
                    use_terms_cache=True,
                    persist_terms_cache=True,
                    use_fetch_cache=True,
                    allow_angles_fallback=allow_angles_fallback,
                    on_progress=on_progress,
                    pass_num=1,
                    cache_mode="main",
                )
                self._clear_fetch_failure(post_id)
                break
            except Exception as exc:
                if not self._is_data_load_fetch_failure(exc):
                    raise
                fail_count = self._record_fetch_failure(post_id)
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "double-process-new-post",
                        "event": "fetch_failed",
                        "pass": 1,
                        "post_id": post_id,
                        "file_name": file_name,
                        "failure_count": fail_count,
                    },
                )
                _LOG.info(
                    "double_process_new_post pass 1 fetch failed post_id={} attempt={}; retrying",
                    post_id,
                    fail_count,
                )
                time.sleep(1.0)

        pass1_total_ms = int((time.perf_counter() - t_pass1) * 1000)
        # region agent log
        write_debug_probe(
            run_id=str(get_run_id() or ""),
            hypothesis_id="H4",
            location="workflows/runner.py:run_double_process_new_post:pass1_end",
            message="double-process pass 1 finished",
            data={"post_id": post_id, "elapsed_ms": pass1_total_ms},
        )
        # endregion
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "pass_1_finished",
                "post_id": post_id,
                "pass": 1,
                "cache_mode": "main",
                "elapsed_ms": pass1_total_ms,
            },
        )
        _LOG.bind(post_id=post_id, pass_num=1, elapsed_ms=pass1_total_ms).info(
            "workflow_progress_double_process_pass_finished"
        )
        first_pass["settings"]["cache_profile"] = "main"
        first_pass["settings"]["cache_paths"] = _workflow_cache_paths(WorkflowConfig())

        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "pass_2_validation_start",
                "post_id": post_id,
            },
        )
        # region agent log
        write_debug_probe(
            run_id=str(get_run_id() or ""),
            hypothesis_id="H4",
            location="workflows/runner.py:run_double_process_new_post:pass2_start",
            message="double-process pass 2 started",
            data={"post_id": post_id, "cache_mode": "validation"},
        )
        # endregion
        t_pass2 = time.perf_counter()
        validation_cfg = _double_process_validation_workflow_config()
        while True:
            try:
                with isolated_workflow_config(validation_cfg):
                    second_pass = self._run_three_stage_post(
                        post_id=post_id,
                        use_terms_cache=True,
                        persist_terms_cache=True,
                        use_fetch_cache=True,
                        allow_angles_fallback=allow_angles_fallback,
                        on_progress=on_progress,
                        pass_num=2,
                        cache_mode="validation",
                    )
                break
            except Exception as exc:
                if not self._is_data_load_fetch_failure(exc):
                    raise
                fail_count = self._record_fetch_failure(post_id)
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "double-process-new-post",
                        "event": "fetch_failed",
                        "pass": 2,
                        "post_id": post_id,
                        "file_name": file_name,
                        "failure_count": fail_count,
                    },
                )
                _LOG.info(
                    "double_process_new_post pass 2 fetch failed post_id={} attempt={}; retrying",
                    post_id,
                    fail_count,
                )
                time.sleep(1.0)

        pass2_total_ms = int((time.perf_counter() - t_pass2) * 1000)
        # region agent log
        write_debug_probe(
            run_id=str(get_run_id() or ""),
            hypothesis_id="H4",
            location="workflows/runner.py:run_double_process_new_post:pass2_end",
            message="double-process pass 2 finished",
            data={"post_id": post_id, "elapsed_ms": pass2_total_ms},
        )
        # endregion
        self._emit(
            on_progress,
            "stage_progress",
            {
                "stage": "double-process-new-post",
                "event": "pass_2_finished",
                "post_id": post_id,
                "pass": 2,
                "cache_mode": "validation",
                "elapsed_ms": pass2_total_ms,
            },
        )
        _LOG.bind(post_id=post_id, pass_num=2, elapsed_ms=pass2_total_ms).info(
            "workflow_progress_double_process_pass_finished"
        )
        second_pass["settings"]["cache_profile"] = "validation"
        second_pass["settings"]["cache_paths"] = _workflow_cache_paths(validation_cfg)

        comparison = {
            stage: first_pass["steps"][stage]["hash"] == second_pass["steps"][stage]["hash"]
            for stage in ("data_load", "research", "gen_angles")
        }
        result = {
            "mode": "double_process_new_post",
            "post_id": post_id,
            "source_file": file_name,
            "passes": {
                "pass_1_cached": first_pass,
                "pass_2_validation": second_pass,
            },
            "stage_hash_match": comparison,
        }
        # region agent log
        write_debug_probe(
            run_id=str(get_run_id() or ""),
            hypothesis_id="H4",
            location="workflows/runner.py:run_double_process_new_post:compare",
            message="double-process stage comparison computed",
            data={"post_id": post_id, **comparison},
        )
        # endregion
        _LOG.info(
            "double_process_new_post post_id={} data_load_match={} research_match={} gen_angles_match={}",
            post_id,
            comparison["data_load"],
            comparison["research"],
            comparison["gen_angles"],
        )
        self._emit(
            on_progress,
            "workflow_done",
            {
                "workflow": "double-process-new-post",
                "post_id": post_id,
                "stage_hash_match": comparison,
            },
        )
        return result

    def run_batch_angles_determinism(
        self,
        post_ids: List[str],
        *,
        step: str = "angles-step",
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Empirically test whether two fresh angle runs (no angles disk cache) match.

        Loads each post from ``step``, builds the same text dictionary as gen_angles,
        runs ``analyze_angles_from_texts(..., use_cache=False)`` twice, and compares
        normalized angle lists (same rules as production preview_post).
        """
        from pipelines.angles.angle_runner import analyze_angles_from_texts

        if not post_ids:
            raise ValueError("post_ids must contain at least one post id")

        self._emit(
            on_progress,
            "workflow_start",
            {
                "workflow": "batch-angles-determinism",
                "step": step,
                "post_count": len(post_ids),
            },
        )

        row_results: List[Dict[str, Any]] = []

        for post_id_raw in post_ids:
            if not post_id_raw.strip():
                row_results.append(
                    {
                        "post_id": post_id_raw,
                        "error": "invalid post_id",
                        "identical": None,
                    }
                )
                continue

            stem = Path(post_id_raw.strip()).stem
            file_name = f"{stem}.json"
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "batch-angles-determinism",
                    "event": "post_start",
                    "post_id": stem,
                    "source_file": file_name,
                },
            )

            try:
                post = self.backend.get_post_local(file_name, step)
            except Exception as exc:
                row_results.append(
                    {
                        "post_id": stem,
                        "source_file": file_name,
                        "error": str(exc),
                        "identical": None,
                    }
                )
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "batch-angles-determinism",
                        "event": "post_error",
                        "post_id": stem,
                        "error": str(exc),
                    },
                )
                continue

            dictionary = self.gen_angles.build_dictionary_for_post(post)
            input_hash = stable_hash(dictionary)

            if not dictionary:
                row = {
                    "post_id": stem,
                    "source_file": file_name,
                    "input_text_blocks": 0,
                    "input_hash": input_hash,
                    "error": "no text blocks for angles input",
                    "identical": None,
                }
                row_results.append(row)
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "batch-angles-determinism",
                        "event": "post_done",
                        **row,
                    },
                )
                continue

            try:
                raw_a = analyze_angles_from_texts(dictionary, use_cache=False)
                raw_b = analyze_angles_from_texts(dictionary, use_cache=False)
            except Exception as exc:
                row = {
                    "post_id": stem,
                    "source_file": file_name,
                    "input_text_blocks": len(dictionary),
                    "input_hash": input_hash,
                    "error": str(exc),
                    "identical": None,
                }
                row_results.append(row)
                self._emit(
                    on_progress,
                    "stage_progress",
                    {
                        "stage": "batch-angles-determinism",
                        "event": "post_error",
                        "post_id": stem,
                        "error": str(exc),
                    },
                )
                continue

            norm_a = _normalized_angles_from_raw(raw_a)
            norm_b = _normalized_angles_from_raw(raw_b)
            h1 = stable_hash(norm_a)
            h2 = stable_hash(norm_b)
            identical = norm_a == norm_b

            row = {
                "post_id": stem,
                "source_file": file_name,
                "input_text_blocks": len(dictionary),
                "input_hash": input_hash,
                "run_1_count": len(norm_a),
                "run_2_count": len(norm_b),
                "run_1_hash": h1,
                "run_2_hash": h2,
                "identical": identical,
            }
            row_results.append(row)
            _LOG.info(
                "batch_angles_determinism post_id={} identical={} run_1_count={} run_2_count={}",
                stem,
                identical,
                len(norm_a),
                len(norm_b),
            )
            self._emit(
                on_progress,
                "stage_progress",
                {
                    "stage": "batch-angles-determinism",
                    "event": "post_done",
                    "post_id": stem,
                    "identical": identical,
                    "run_1_hash": h1,
                    "run_2_hash": h2,
                },
            )

        tested_ok = [r for r in row_results if r.get("error") is None]
        all_identical = bool(tested_ok) and all(r.get("identical") is True for r in tested_ok)

        out = {
            "mode": "batch_angles_determinism",
            "step": step,
            "posts_requested": len(post_ids),
            "posts_succeeded": len(tested_ok),
            "all_identical": all_identical,
            "results": row_results,
        }
        self._emit(
            on_progress,
            "workflow_done",
            {
                "workflow": "batch-angles-determinism",
                "all_identical": all_identical,
                "posts_succeeded": len(tested_ok),
            },
        )
        return out

    def run_full_pipeline(
        self,
        start_step: str = "filter-url-unresolved",
        count: int = 1,
        payload: Optional[str] = None,
        on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict]:
        """
        Run full pipeline from start_step to final-step.
        
        Args:
            start_step: Starting step name
            count: Number of posts to process
        
        Returns:
            List of processed posts
        """
        results = []
        self._emit(
            on_progress,
            "workflow_start",
            {
                "workflow": "full",
                "start_step": start_step,
                "count": count,
                "payload_provided": bool(payload),
            },
        )
        
        if start_step == "filter-url-unresolved":
            data_results = self._call_with_optional_progress(
                self.run_data_load,
                on_progress,
                count=count,
            )
            if not data_results:
                self._emit(
                    on_progress,
                    "workflow_done",
                    {"workflow": "full", "processed_count": 0},
                )
                return results

            # Explicit stage handoff: research what we just loaded.
            self._emit(
                on_progress,
                "stage_start",
                {"stage": "research", "source": "data-load", "count": len(data_results)},
            )
            research_results = self.research.process_post_objects(
                posts=data_results,
                step="filter-researched",
            )
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "research", "processed_count": len(research_results)},
            )
            if not research_results:
                self._emit(
                    on_progress,
                    "workflow_done",
                    {"workflow": "full", "processed_count": 0},
                )
                return results

            # Explicit stage handoff: angle what we just researched.
            self._emit(
                on_progress,
                "stage_start",
                {"stage": "gen-angles", "source": "research", "count": len(research_results)},
            )
            final_results = self.gen_angles.process_post_objects(
                posts=research_results,
                step="angles-step",
            )
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "gen-angles", "processed_count": len(final_results)},
            )
            self._emit(
                on_progress,
                "workflow_done",
                {"workflow": "full", "processed_count": len(final_results)},
            )
            return final_results

        if start_step == "filter-researched":
            research_results = self._call_with_optional_progress(
                self.run_research,
                on_progress,
                count=count,
            )
            if not research_results:
                self._emit(
                    on_progress,
                    "workflow_done",
                    {"workflow": "full", "processed_count": 0},
                )
                return results
            self._emit(
                on_progress,
                "stage_start",
                {"stage": "gen-angles", "source": "research", "count": len(research_results)},
            )
            final_results = self.gen_angles.process_post_objects(
                posts=research_results,
                step="angles-step",
            )
            self._emit(
                on_progress,
                "stage_done",
                {"stage": "gen-angles", "processed_count": len(final_results)},
            )
            self._emit(
                on_progress,
                "workflow_done",
                {"workflow": "full", "processed_count": len(final_results)},
            )
            return final_results

        if start_step == "angles-step":
            results = self._call_with_optional_progress(
                self.run_gen_angles,
                on_progress,
                count=count,
            )
            self._emit(
                on_progress,
                "workflow_done",
                {"workflow": "full", "processed_count": len(results)},
            )
            return results

        raise ValueError(f"Unsupported start_step: {start_step}")
