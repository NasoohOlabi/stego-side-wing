"""Generate angles from post content."""
import time
from typing import Any, Dict, List

from loguru import logger

from infrastructure.config import (
    get_workflow_llm_backend,
    resolve_workflow_llm_provider_and_model,
)
from infrastructure.json_logging import get_trace_id
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.utils.debug_probe import write_debug_probe
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.text_utils import (
    build_post_text_dictionary,
    flatten_comments,
    parse_json_array_response,
)
from workflows.utils.angles_llm_config import (
    SYSTEM_PROMPT as ANGLES_SYSTEM_PROMPT,
    TEMPERATURE as ANGLES_TEMPERATURE,
    USER_PROMPT_TEMPLATE as ANGLES_USER_PROMPT_TEMPLATE,
    angles_model_name,
)
from workflows.utils.workflow_llm_prompts import get_prompts

_LOG = logger.bind(component="GenAnglesPipeline")


def _gen_angles_bind_log():
    tid = get_trace_id()
    return _LOG.bind(trace_id=tid if tid else "")


def _elapsed_ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


def _probe_llm_run_id(pipeline: Any) -> str:
    llm = getattr(pipeline, "llm", None)
    if llm is None:
        return ""
    meta = getattr(llm, "last_call_metadata", {}) or {}
    return str(meta.get("run_id") or "")


class GenAnglesPipeline:
    """
    Stateful orchestration for angle generation: owns backend and LLM adapters, workflow
    config, and the last batch processing summary for observability and CLI/API callers.
    """

    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.llm = LLMAdapter()
        self.config = get_config()
        self._last_batch_summary: Dict[str, Any] = {}

    def _flatten_comments(self, comments: List[Dict]) -> List[Dict]:
        """Flatten nested comment structure."""
        return flatten_comments(comments)

    def _build_dictionary(self, post: Dict) -> List[str]:
        """Build dictionary of texts from post."""
        return build_post_text_dictionary(post)

    def build_dictionary_for_post(self, post: Dict[str, Any]) -> List[str]:
        """Public alias for workflow runner / tools that need the same inputs as gen_angles."""
        return self._build_dictionary(post)

    def preview_post(
        self,
        post: Dict[str, Any],
        allow_fallback: bool = False,
    ) -> Dict[str, Any]:
        """Generate angles without mutating or saving artifacts."""
        post_id = str(post.get("id") or "<unknown>")
        dictionary = self._build_dictionary(post)
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
            "system_prompt_hash": stable_hash(ANGLES_SYSTEM_PROMPT),
            "user_prompt_template_hash": stable_hash(ANGLES_USER_PROMPT_TEMPLATE),
            "used_fallback": False,
        }
        write_debug_probe(
            run_id=None,
            hypothesis_id="H3",
            location="workflows/pipelines/gen_angles.py:preview_post:begin",
            message="angle preview started",
            data={
                "post_id": post_id,
                "input_count": len(dictionary),
                "allow_fallback": allow_fallback,
                "input_hash": report["input_hash"],
            },
        )
        if not dictionary:
            report.update({"angles": [], "angles_hash": stable_hash([]), "options_count": 0})
            _gen_angles_bind_log().info(
                "gen_angles_preview_complete",
                path="empty_dictionary",
                post_id=post_id,
                elapsed_ms_total=_elapsed_ms(t_preview),
                input_count=0,
                angles_count=0,
            )
            return {"post": dict(post, angles=[], options_count=0), "report": report}

        t_an = time.perf_counter()
        try:
            response = self.backend.analyze_angles(dictionary)
            analyze_ms = _elapsed_ms(t_an)
            results = response.get("results", [])
            angles = []
            for result in results:
                if isinstance(result, dict):
                    angle: Dict[str, Any] = {
                        "source_quote": result.get("source_quote", ""),
                        "tangent": result.get("tangent", ""),
                        "category": result.get("category", ""),
                    }
                    sd = result.get("source_document")
                    if isinstance(sd, int):
                        angle["source_document"] = sd
                    if angle["source_quote"] and angle["tangent"] and angle["category"]:
                        angles.append(angle)
            processed_post = dict(post)
            processed_post["angles"] = angles
            processed_post["options_count"] = len(angles)
            report.update(
                {
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                }
            )
            write_debug_probe(
                run_id=_probe_llm_run_id(self),
                hypothesis_id="H3",
                location="workflows/pipelines/gen_angles.py:preview_post:success",
                message="angle preview succeeded",
                data={
                    "post_id": post_id,
                    "input_count": len(dictionary),
                    "angles_count": len(angles),
                    "allow_fallback": allow_fallback,
                },
            )
            _gen_angles_bind_log().info(
                "gen_angles_preview_complete",
                path="analyze_angles",
                post_id=post_id,
                elapsed_ms_total=_elapsed_ms(t_preview),
                elapsed_ms_analyze_angles=analyze_ms,
                input_count=len(dictionary),
                angles_count=len(angles),
                angles_hash=report["angles_hash"],
            )
            return {"post": processed_post, "report": report}
        except Exception as e:
            primary_ms = _elapsed_ms(t_an)
            write_debug_probe(
                run_id=_probe_llm_run_id(self),
                hypothesis_id="H3",
                location="workflows/pipelines/gen_angles.py:preview_post:failure",
                message="angle preview primary path failed",
                data={
                    "post_id": post_id,
                    "input_count": len(dictionary),
                    "allow_fallback": allow_fallback,
                    "error_kind": type(e).__name__,
                },
            )
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
            write_debug_probe(
                run_id=_probe_llm_run_id(self),
                hypothesis_id="H3",
                location="workflows/pipelines/gen_angles.py:preview_post:fallback",
                message="angle preview falling back to llm-generated angles",
                data={
                    "post_id": post_id,
                    "input_count": len(dictionary),
                    "allow_fallback": allow_fallback,
                    "error_kind": type(e).__name__,
                },
            )
            t_fb = time.perf_counter()
            angles = self._generate_angles_llm(dictionary)
            fallback_ms = _elapsed_ms(t_fb)
            for a in angles:
                a.setdefault("source_document", 0)
            processed_post = dict(post)
            processed_post["angles"] = angles
            processed_post["options_count"] = len(angles)
            report.update(
                {
                    "used_fallback": True,
                    "angles": angles,
                    "angles_hash": stable_hash(angles),
                    "options_count": len(angles),
                    "fallback_error": str(e),
                }
            )
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
            )
            return {"post": processed_post, "report": report}

    def generate_angles(self, post: Dict, allow_fallback: bool = False) -> List[Dict[str, Any]]:
        """
        Generate angles from post content.

        Args:
            post: Post dictionary with content, search_results, comments

        Returns:
            List of angle dictionaries
        """
        return list(self.preview_post(post, allow_fallback=allow_fallback)["report"]["angles"])

    def _generate_angles_llm(self, texts: List[str]) -> List[Dict[str, Any]]:
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
        post: Dict,
        step: str = "angles-step",
        allow_fallback: bool = False,
    ) -> Dict:
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
    ) -> List[Dict]:
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

        posts: List[Dict[str, Any]] = []
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
        processed_posts = self.process_post_objects(posts=posts, step=step)
        processing_summary = dict(getattr(self, "_last_batch_summary", {}))
        processing_failed = int(processing_summary.get("failed_count", 0) or 0)
        self._last_batch_summary = {
            "step": step,
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
        posts: List[Dict[str, Any]],
        step: str = "angles-step",
        allow_fallback: bool = False,
    ) -> List[Dict[str, Any]]:
        """Process already-loaded post objects and persist angle-enriched versions."""
        processed_posts: List[Dict[str, Any]] = []
        for post in posts:
            post_id = post.get("id", "<unknown>")
            t_post = time.perf_counter()
            try:
                processed = self.process_post(post, step, allow_fallback=allow_fallback)
                self.backend.save_post_local(processed, step=step)
                processed_posts.append(processed)
                _gen_angles_bind_log().info(
                    "gen_angles_post_persisted",
                    post_id=post_id,
                    step=step,
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
    ) -> Dict[str, Any]:
        """
        Process one post by ID and persist angle output.

        Args:
            post_id: Post identifier without `.json`
            step: Workflow step name

        Returns:
            Processed post dictionary with angles
        """
        file_name = f"{post_id}.json"
        post = self.backend.get_post_local(file_name, step)
        results = self.process_post_objects(
            posts=[post],
            step=step,
            allow_fallback=allow_fallback,
        )
        if not results:
            raise RuntimeError(f"GenAngles returned no result for post {post_id}")
        return results[0]
