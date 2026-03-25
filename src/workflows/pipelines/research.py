"""Research pipeline: generate search terms, search, and fetch content."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import logging
import os
import time
from typing import Any, Dict, List

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.contracts import FetchUrlResult
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline
from workflows.utils.protocol_utils import stable_hash, text_preview

logger = logging.getLogger(__name__)

# Per-URL fetch in research; avoids indefinite hang if crawl/browser stalls.
_RESEARCH_FETCH_TIMEOUT_SEC = float(os.environ.get("RESEARCH_FETCH_TIMEOUT_SEC", "180"))
# Retries after a timed-out attempt (e.g. 1 => two attempts total per URL).
_RESEARCH_FETCH_RETRIES = max(0, int(os.environ.get("RESEARCH_FETCH_RETRIES", "1")))


def _term_preview(term: str, max_len: int = 160) -> str:
    t = term.replace("\n", " ").strip()
    return t if len(t) <= max_len else t[: max_len - 3] + "..."


def _fetch_attempts_total() -> int:
    return 1 + _RESEARCH_FETCH_RETRIES


class ResearchPipeline:
    """Pipeline for researching posts."""
    
    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.gen_terms = GenSearchTermsPipeline()
        self.fetch_content = FetchUrlContentPipeline()

    def _fetch_url_with_timeout_retries(
        self,
        post_id: str,
        url: str,
        use_fetch_cache: bool,
    ) -> FetchUrlResult:
        """Run fetch in an isolated worker with per-attempt timeout; retry on timeout."""
        attempts = _fetch_attempts_total()
        ts = _RESEARCH_FETCH_TIMEOUT_SEC
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                logger.info(
                    "research_fetch_retry",
                    extra={
                        "event": "research",
                        "post_id": post_id,
                        "url": url,
                        "attempt": attempt,
                        "attempts_total": attempts,
                        "timeout_sec": ts,
                    },
                )
            ex = ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(self.fetch_content.fetch, url, use_fetch_cache)
            try:
                out = fut.result(timeout=ts)
            except FutureTimeoutError:
                ex.shutdown(wait=False)
                logger.warning(
                    "research_fetch_timed_out",
                    extra={
                        "event": "research",
                        "post_id": post_id,
                        "url": url,
                        "attempt": attempt,
                        "attempts_total": attempts,
                        "timeout_sec": ts,
                        "will_retry": attempt < attempts,
                    },
                )
                if attempt >= attempts:
                    err = f"Timed out after {attempts} attempt(s) ({ts}s each)"
                    logger.error(
                        "research_fetch_timed_out_exhausted",
                        extra={
                            "event": "research",
                            "post_id": post_id,
                            "url": url,
                            "attempts_total": attempts,
                            "timeout_sec": ts,
                        },
                    )
                    return FetchUrlResult(url=url, success=False, error=err)
                continue
            except Exception as e:
                ex.shutdown(wait=False)
                logger.exception(
                    "research_fetch_failed post_id=%s url=%s attempt=%s",
                    post_id,
                    url,
                    attempt,
                )
                return FetchUrlResult(url=url, success=False, error=str(e))
            else:
                ex.shutdown(wait=True)
                return out
        raise RuntimeError("research fetch retry loop fell through")

    @staticmethod
    def _search_summary(result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": result.get("title", ""),
            "link": result.get("link", ""),
            "snippet": result.get("snippet", ""),
            "snippet_hash": stable_hash(result.get("snippet", "")),
        }

    def preview_post(
        self,
        post: Dict[str, Any],
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
    ) -> Dict[str, Any]:
        """Run the live research protocol without saving the output."""
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must have 'id' field")

        if not force and not self._is_new_post(post):
            return {
                "post": post,
                "report": {
                    "post_id": post_id,
                    "step": step,
                    "skipped": True,
                    "reason": "post already contains search_results",
                    "search_terms": [],
                    "search_terms_hash": stable_hash([]),
                    "searches": [],
                    "selected_results": [],
                    "fetched_pages": [],
                    "search_results": post.get("search_results", []),
                    "search_results_hash": stable_hash(post.get("search_results", [])),
                    "search_results_count": len(post.get("search_results", []) or []),
                },
            }

        terms_report = self.gen_terms.preview_generation(
            post_id=post_id,
            post_title=post.get("title"),
            post_text=post.get("selftext") or post.get("text"),
            post_url=post.get("url"),
            use_cache=use_terms_cache,
            persist_cache=persist_terms_cache,
        )
        search_terms = list(terms_report.get("terms", []))
        if not search_terms:
            report = {
                "post_id": post_id,
                "step": step,
                "search_terms": [],
                "search_terms_hash": stable_hash([]),
                "terms_report": terms_report,
                "searches": [],
                "selected_results": [],
                "fetched_pages": [],
                "search_results": [],
                "search_results_hash": stable_hash([]),
                "search_results_count": 0,
                "error": terms_report.get("error"),
            }
            logger.warning(
                "research preview has no terms post_id=%s error=%s",
                post_id,
                terms_report.get("error"),
            )
            post_copy = dict(post)
            post_copy["search_results"] = []
            return {"post": post_copy, "report": report}

        all_search_results: List[Dict[str, Any]] = []
        search_events: List[Dict[str, Any]] = []
        seen_links: set[str] = set()
        n_terms = len(search_terms)
        logger.info(
            "research_google_phase_begin",
            extra={
                "event": "research",
                "post_id": post_id,
                "search_term_count": n_terms,
            },
        )

        for idx, term in enumerate(search_terms, start=1):
            t_search = time.perf_counter()
            logger.info(
                "research_google_query_begin",
                extra={
                    "event": "research",
                    "post_id": post_id,
                    "term_index": idx,
                    "term_total": n_terms,
                    "term_preview": _term_preview(term),
                },
            )
            try:
                search_response = self.backend.google_search(query=term, first=1, count=10)
                raw_results = search_response.get("results", [])
            except Exception as e:
                logger.exception("google search failed post_id=%s term=%s", post_id, term)
                raise RuntimeError(
                    f"Google search failed for post {post_id} and term '{term}': {e}"
                ) from e
            logger.info(
                "research_google_query_done",
                extra={
                    "event": "research",
                    "post_id": post_id,
                    "term_index": idx,
                    "term_total": n_terms,
                    "elapsed_ms": int((time.perf_counter() - t_search) * 1000),
                    "raw_hits": len(raw_results),
                },
            )

            selected_for_term: List[Dict[str, Any]] = []
            skipped_for_term: List[Dict[str, Any]] = []
            for result in raw_results:
                link = result.get("link", "")
                if not link:
                    skipped_for_term.append({"reason": "missing_link"})
                    continue
                if link.endswith(".pdf"):
                    skipped_for_term.append({"reason": "pdf", "link": link})
                    continue
                if link in seen_links:
                    skipped_for_term.append({"reason": "duplicate", "link": link})
                    continue
                seen_links.add(link)
                summary = self._search_summary(result)
                selected_for_term.append(summary)
                all_search_results.append(result)

            search_events.append(
                {
                    "term": term,
                    "term_hash": stable_hash(term),
                    "returned_count": len(raw_results),
                    "selected_count": len(selected_for_term),
                    "selected_results": selected_for_term,
                    "skipped": skipped_for_term,
                }
            )

        fetched_texts: List[str] = []
        fetched_pages: List[Dict[str, Any]] = []
        batch_size = 3
        total_urls = len(all_search_results)
        n_batches = (total_urls + batch_size - 1) // batch_size if total_urls else 0
        logger.info(
            "research_url_fetch_phase_begin",
            extra={
                "event": "research",
                "post_id": post_id,
                "unique_result_links": len(all_search_results),
                "urls_to_fetch": total_urls,
                "batch_size": batch_size,
                "batch_count": n_batches,
                "fetch_timeout_sec": _RESEARCH_FETCH_TIMEOUT_SEC,
                "fetch_retries": _RESEARCH_FETCH_RETRIES,
                "fetch_attempts_total": _fetch_attempts_total(),
            },
        )

        batch_num = 0
        for i in range(0, len(all_search_results), batch_size):
            batch = all_search_results[i : i + batch_size]
            urls: List[str] = [
                str(link) for link in (r.get("link") for r in batch) if link
            ]
            if not urls:
                continue
            batch_num += 1
            t_batch = time.perf_counter()
            logger.info(
                "research_fetch_batch_begin",
                extra={
                    "event": "research",
                    "post_id": post_id,
                    "batch_index": batch_num,
                    "batch_url_count": len(urls),
                    "urls": urls[:5],
                },
            )
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                future_items = [
                    (
                        url,
                        pool.submit(
                            self._fetch_url_with_timeout_retries,
                            post_id,
                            url,
                            use_fetch_cache,
                        ),
                    )
                    for url in urls
                ]
                for url, future in future_items:
                    try:
                        fetch_result = future.result()
                        page_report: Dict[str, Any] = {
                            "url": url,
                            "success": fetch_result.success,
                            "content_type": fetch_result.content_type,
                            "error": fetch_result.error,
                            "use_cache": use_fetch_cache,
                        }
                        if fetch_result.success and fetch_result.text:
                            fetched_texts.append(fetch_result.text)
                            page_report.update(
                                {
                                    "text_hash": stable_hash(fetch_result.text),
                                    "text_length": len(fetch_result.text),
                                    "text_preview": text_preview(fetch_result.text),
                                }
                            )
                        fetched_pages.append(page_report)
                    except Exception as e:
                        logger.exception("content fetch failed post_id=%s url=%s", post_id, url)
                        fetched_pages.append(
                            {
                                "url": url,
                                "success": False,
                                "error": str(e),
                                "use_cache": use_fetch_cache,
                            }
                        )
            logger.info(
                "research_fetch_batch_done",
                extra={
                    "event": "research",
                    "post_id": post_id,
                    "batch_index": batch_num,
                    "elapsed_ms": int((time.perf_counter() - t_batch) * 1000),
                },
            )

        post_copy = dict(post)
        post_copy["search_results"] = fetched_texts
        report = {
            "post_id": post_id,
            "step": step,
            "search_terms": search_terms,
            "search_terms_hash": stable_hash(search_terms),
            "terms_report": terms_report,
            "searches": search_events,
            "selected_results": [
                self._search_summary(result) for result in all_search_results
            ],
            "fetched_pages": fetched_pages,
            "search_results": fetched_texts,
            "search_results_hash": stable_hash(fetched_texts),
            "search_results_count": len(fetched_texts),
        }
        logger.info(
            "research preview post_id=%s terms=%s selected_links=%s fetched=%s hash=%s",
            post_id,
            len(search_terms),
            len(all_search_results),
            len(fetched_texts),
            report["search_results_hash"],
        )
        return {"post": post_copy, "report": report}

    @staticmethod
    def _is_new_post(post: Dict[str, Any]) -> bool:
        """
        Mirror n8n "New" IF node semantics:
        treat post as new when search_results is missing, empty,
        or contains only blank strings.
        """
        search_results = post.get("search_results")
        if search_results is None:
            return True

        if isinstance(search_results, list):
            return len([x for x in search_results if isinstance(x, str) and x.strip()]) == 0

        if isinstance(search_results, dict):
            flattened: List[Any] = []
            for value in search_results.values():
                if isinstance(value, list):
                    flattened.extend(value)
                else:
                    flattened.append(value)
            return len([x for x in flattened if isinstance(x, str) and x.strip()]) == 0

        return False
    
    def research_post(
        self,
        post: Dict,
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
    ) -> Dict:
        """
        Research a single post: generate terms, search, fetch content.
        
        Args:
            post: Post dictionary
            step: Workflow step name
        
        Returns:
            Enriched post dictionary with search_results
        """
        preview = self.preview_post(
            post=post,
            step=step,
            force=force,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )
        return preview["post"]
    
    def process_posts(
        self,
        step: str = "filter-researched",
        count: int = 1,
        offset: int = 1,
    ) -> List[Dict]:
        """
        Process multiple posts for research.
        
        Args:
            step: Workflow step name
            count: Number of posts to process
            offset: Offset for pagination
        
        Returns:
            List of researched post dictionaries
        """
        # Get list of post filenames
        posts_list = self.backend.posts_list(step=step, count=count, offset=offset)
        file_names = posts_list.get("fileNames", [])
        
        if not file_names:
            return []
        
        posts: List[Dict[str, Any]] = []
        for file_name in file_names:
            try:
                posts.append(self.backend.get_post_local(file_name, step))
            except Exception:
                logger.exception("research load failed for file=%s", file_name)
        return self.process_post_objects(posts=posts, step=step)

    def process_post_objects(
        self,
        posts: List[Dict[str, Any]],
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
    ) -> List[Dict[str, Any]]:
        """Process already-loaded post objects and persist researched versions."""
        researched_posts: List[Dict[str, Any]] = []
        for post in posts:
            post_id = post.get("id", "<unknown>")
            try:
                was_new = self._is_new_post(post)
                researched = self.research_post(
                    post,
                    step,
                    force=force,
                    use_terms_cache=use_terms_cache,
                    persist_terms_cache=persist_terms_cache,
                    use_fetch_cache=use_fetch_cache,
                )
                self.backend.save_post_local(researched, step=step)
                if was_new:
                    try:
                        self.backend.save_post(researched, step=step)
                    except Exception as e:
                        logger.exception("research backend save failed for post_id=%s", post_id)
                researched_posts.append(researched)
            except Exception as e:
                raise RuntimeError(f"Error processing post {post_id}: {e}") from e
        return researched_posts

    def process_post_id(
        self,
        post_id: str,
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Process one post by ID and persist researched output.

        Args:
            post_id: Post identifier without `.json`
            step: Workflow step name

        Returns:
            Researched post dictionary
        """
        file_name = f"{post_id}.json"
        post = self.backend.get_post_local(file_name, step)
        results = self.process_post_objects(
            posts=[post],
            step=step,
            force=force,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )
        if not results:
            raise RuntimeError(f"Research returned no result for post {post_id}")
        return results[0]
