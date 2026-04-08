"""DataLoad pipeline: fetch URL content for unresolved posts."""
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

from loguru import logger

from infrastructure.json_logging import TAG_WORKFLOW
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline
from workflows.utils.protocol_utils import stable_hash, text_preview


def _url_host(url: str) -> Optional[str]:
    try:
        netloc = urlparse(url).netloc
        return netloc or None
    except Exception:
        return None


def _log_event(
    log: Any,
    operation_id: str,
    message: str,
    **fields: Any,
) -> None:
    log.info(
        message,
        data_load_operation_id=operation_id,
        tags=[TAG_WORKFLOW],
        event="data_load",
        **fields,
    )


class DataLoadPipeline:
    """Loads each post's article body via URL fetch; holds backend and fetch pipeline clients."""

    def __init__(self) -> None:
        self.backend = BackendAPIAdapter()
        self.fetch_pipeline = FetchUrlContentPipeline()
        self._log = logger.bind(component="DataLoadPipeline")

    def _ensure_log(self) -> None:
        if not hasattr(self, "_log"):
            object.__setattr__(self, "_log", logger.bind(component="DataLoadPipeline"))

    def _fetch_and_merge_post(
        self,
        operation_id: str,
        file_name: str,
        step: str,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        t_fetch = time.perf_counter()
        post = self.backend.get_post_local(file_name, step)
        pid = post.get("id")
        url = post.get("url")
        if not url:
            ms = int((time.perf_counter() - t_fetch) * 1000)
            _log_event(
                self._log,
                operation_id,
                "data_load_post_skip",
                file_name=file_name,
                post_id=pid,
                outcome="no_url",
                elapsed_ms=ms,
            )
            return None, {"outcome": "no_url"}
        fetch_result = self.fetch_pipeline.fetch(url, use_cache=True)
        fetch_ms = int((time.perf_counter() - t_fetch) * 1000)
        ok = bool(fetch_result.success and fetch_result.text)
        _log_event(
            self._log,
            operation_id,
            "data_load_fetch_complete",
            file_name=file_name,
            post_id=pid,
            url_host=_url_host(str(url)),
            fetch_success=fetch_result.success,
            selftext_chars=len(fetch_result.text or ""),
            elapsed_ms_fetch=fetch_ms,
            outcome="fetched" if ok else "fetch_empty_or_failed",
        )
        if ok:
            post["selftext"] = fetch_result.text
            return post, {"outcome": "ok"}
        return None, {"outcome": "bad_fetch"}

    def _process_batch_files(
        self,
        operation_id: str,
        batch: List[str],
        step: str,
    ) -> List[Dict[str, Any]]:
        batch_results: List[Dict[str, Any]] = []
        for file_name in batch:
            try:
                merged, _ = self._fetch_and_merge_post(operation_id, file_name, step)
                if merged is not None:
                    batch_results.append(merged)
            except Exception:
                self._log.exception(
                    "data_load_file_failed",
                    data_load_operation_id=operation_id,
                    tags=[TAG_WORKFLOW],
                    event="data_load",
                    file_name=file_name,
                    step=step,
                )
                continue
        return batch_results

    def _persist_batch(
        self,
        operation_id: str,
        batch_results: List[Dict[str, Any]],
        processed_posts: List[Dict[str, Any]],
    ) -> None:
        for post in batch_results:
            post_id = post.get("id")
            if not post_id or not (post.get("selftext") and post["selftext"].strip()):
                continue
            try:
                t_save = time.perf_counter()
                self.backend.save_post_local(post, step="filter-url-unresolved")
                save_ms = int((time.perf_counter() - t_save) * 1000)
                processed_posts.append(post)
                _log_event(
                    self._log,
                    operation_id,
                    "data_load_save_complete",
                    post_id=post_id,
                    elapsed_ms_save=save_ms,
                )
            except Exception:
                self._log.exception(
                    "data_load_save_failed",
                    data_load_operation_id=operation_id,
                    tags=[TAG_WORKFLOW],
                    event="data_load",
                    post_id=post_id,
                )

    def _resolve_post_filenames(
        self,
        step: str,
        count: int,
        offset: int,
    ) -> Tuple[List[str], int]:
        t_list = time.perf_counter()
        posts_list = self.backend.posts_list(step=step, count=count, offset=offset)
        list_ms = int((time.perf_counter() - t_list) * 1000)
        return posts_list.get("fileNames", []), list_ms

    def _finish_process_posts_empty(
        self,
        operation_id: str,
        step: str,
        t_run: float,
    ) -> List[Dict[str, Any]]:
        total_ms = int((time.perf_counter() - t_run) * 1000)
        _log_event(
            self._log,
            operation_id,
            "data_load_process_posts_end",
            step=step,
            posts_saved=0,
            files_processed=0,
            batch_count=0,
            elapsed_ms_total=total_ms,
        )
        return []

    def _finalize_process_posts(
        self,
        operation_id: str,
        step: str,
        t_run: float,
        processed_posts: List[Dict[str, Any]],
        files_done: int,
        n_batches: int,
    ) -> List[Dict[str, Any]]:
        total_ms = int((time.perf_counter() - t_run) * 1000)
        _log_event(
            self._log,
            operation_id,
            "data_load_process_posts_end",
            step=step,
            posts_saved=len(processed_posts),
            files_processed=files_done,
            batch_count=n_batches,
            elapsed_ms_total=total_ms,
        )
        return processed_posts

    def _time_batch_fetch(
        self,
        operation_id: str,
        batch: List[str],
        step: str,
    ) -> Tuple[List[Dict[str, Any]], int]:
        t_batch = time.perf_counter()
        batch_results = self._process_batch_files(operation_id, batch, step)
        return batch_results, int((time.perf_counter() - t_batch) * 1000)

    def _drive_post_batches(
        self,
        operation_id: str,
        file_names: List[str],
        batch_size: int,
        step: str,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        processed: List[Dict[str, Any]] = []
        batch_count = 0
        files_processed = 0
        for i in range(0, len(file_names), batch_size):
            batch = file_names[i : i + batch_size]
            batch_results, batch_ms = self._time_batch_fetch(operation_id, batch, step)
            files_processed += len(batch)
            _log_event(
                self._log,
                operation_id,
                "data_load_batch_complete",
                batch_index=batch_count,
                batch_files=len(batch),
                batch_ready_to_save=len(batch_results),
                elapsed_ms_batch=batch_ms,
            )
            batch_count += 1
            self._persist_batch(operation_id, batch_results, processed)
        return processed, files_processed, batch_count

    def process_posts(
        self,
        step: str = "filter-url-unresolved",
        batch_size: int = 5,
        count: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        self._ensure_log()
        operation_id = str(uuid4())
        t_run = time.perf_counter()
        file_names, list_ms = self._resolve_post_filenames(step, count, offset)
        _log_event(
            self._log,
            operation_id,
            "data_load_process_posts_begin",
            step=step,
            count=count,
            offset=offset,
            batch_size=batch_size,
            files_count=len(file_names),
            elapsed_ms_posts_list=list_ms,
        )
        if not file_names:
            return self._finish_process_posts_empty(operation_id, step, t_run)
        processed_posts, files_done, n_batches = self._drive_post_batches(
            operation_id, file_names, batch_size, step
        )
        return self._finalize_process_posts(
            operation_id, step, t_run, processed_posts, files_done, n_batches
        )

    def preview_post(
        self,
        post: Dict,
        *,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """Fetch URL content for an in-memory post dict (receiver / object workflows)."""
        self._ensure_log()
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must have 'id' field")
        url = post.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Post {post_id} does not contain a valid 'url'")

        op_id = str(uuid4())
        t0 = time.perf_counter()
        fetch_result = self.fetch_pipeline.fetch(url.strip(), use_cache=use_cache)
        fetch_ms = int((time.perf_counter() - t0) * 1000)
        report: Dict[str, Any] = {
            "post_id": str(post_id),
            "step": None,
            "url": url.strip(),
            "use_cache": use_cache,
            "fetch_success": fetch_result.success,
            "content_type": fetch_result.content_type,
            "error": fetch_result.error,
            "elapsed_ms_fetch": fetch_ms,
        }
        post_copy = dict(post)
        if not fetch_result.success or not fetch_result.text:
            _log_event(
                self._log,
                op_id,
                "data_load_preview_post_complete",
                post_id=post_id,
                outcome="fetch_failed",
                elapsed_ms_fetch=fetch_ms,
                use_cache=use_cache,
                error=fetch_result.error,
            )
            return {"post": post_copy, "report": report}

        post_copy["selftext"] = fetch_result.text
        report.update(
            {
                "selftext_length": len(fetch_result.text),
                "selftext_hash": stable_hash(fetch_result.text),
                "selftext_preview": text_preview(fetch_result.text),
            }
        )
        _log_event(
            self._log,
            op_id,
            "data_load_preview_post_complete",
            post_id=post_id,
            outcome="ok",
            elapsed_ms_fetch=fetch_ms,
            use_cache=use_cache,
            selftext_length=report["selftext_length"],
            selftext_hash=report["selftext_hash"],
        )
        return {"post": post_copy, "report": report}

    def preview_post_id(
        self,
        post_id: str,
        step: str = "filter-url-unresolved",
        use_cache: bool = True,
    ) -> Dict:
        """Fetch one post live without mutating step artifacts."""
        self._ensure_log()
        file_name = f"{post_id}.json"
        post = self.backend.get_post_local(file_name, step)
        url = post.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Post {post_id} does not contain a valid 'url'")

        op_id = str(uuid4())
        t0 = time.perf_counter()
        fetch_result = self.fetch_pipeline.fetch(url.strip(), use_cache=use_cache)
        fetch_ms = int((time.perf_counter() - t0) * 1000)
        report: Dict[str, Any] = {
            "post_id": post_id,
            "step": step,
            "url": url.strip(),
            "use_cache": use_cache,
            "fetch_success": fetch_result.success,
            "content_type": fetch_result.content_type,
            "error": fetch_result.error,
            "elapsed_ms_fetch": fetch_ms,
        }
        if not fetch_result.success or not fetch_result.text:
            _log_event(
                self._log,
                op_id,
                "data_load_preview_post_id_complete",
                post_id=post_id,
                outcome="fetch_failed",
                elapsed_ms_fetch=fetch_ms,
                use_cache=use_cache,
                error=fetch_result.error,
            )
            return {"post": post, "report": report}

        post["selftext"] = fetch_result.text
        report.update(
            {
                "selftext_length": len(fetch_result.text),
                "selftext_hash": stable_hash(fetch_result.text),
                "selftext_preview": text_preview(fetch_result.text),
            }
        )
        _log_event(
            self._log,
            op_id,
            "data_load_preview_post_id_complete",
            post_id=post_id,
            outcome="ok",
            elapsed_ms_fetch=fetch_ms,
            use_cache=use_cache,
            selftext_length=report["selftext_length"],
            selftext_hash=report["selftext_hash"],
        )
        return {"post": post, "report": report}

    def process_post_id(
        self,
        post_id: str,
        step: str = "filter-url-unresolved",
        use_cache: bool = True,
    ) -> Dict:
        """
        Process a single post by ID and persist the DataLoad output.

        Args:
            post_id: Post identifier without `.json`
            step: Workflow step name

        Returns:
            Processed post dictionary
        """
        self._ensure_log()
        op_id = str(uuid4())
        t0 = time.perf_counter()
        preview = self.preview_post_id(post_id=post_id, step=step, use_cache=use_cache)
        post = preview["post"]
        report = preview["report"]
        if not report.get("fetch_success") or not post.get("selftext"):
            raise RuntimeError(
                f"Failed to fetch URL content for post {post_id}: {report.get('error')}"
            )
        t_save = time.perf_counter()
        self.backend.save_post_local(post, step=step)
        save_ms = int((time.perf_counter() - t_save) * 1000)
        total_ms = int((time.perf_counter() - t0) * 1000)
        _log_event(
            self._log,
            op_id,
            "data_load_process_post_id_complete",
            post_id=post_id,
            step=step,
            elapsed_ms_save=save_ms,
            elapsed_ms_total=total_ms,
            selftext_length=len(post.get("selftext") or ""),
        )
        return post
