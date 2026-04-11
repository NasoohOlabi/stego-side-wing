"""Research pipeline: generate search terms, search, and fetch content."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import os
import time
from typing import Any, Dict, List
from uuid import uuid4

from loguru import logger

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.contracts import FetchUrlResult
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.research_relevance_debug import (
    research_debug_log_dir,
    tokenize,
    write_research_results_debug,
    write_research_terms_debug,
)

# Per-URL fetch in research; avoids indefinite hang if crawl/browser stalls.
_RESEARCH_FETCH_TIMEOUT_SEC = float(os.environ.get("RESEARCH_FETCH_TIMEOUT_SEC", "180"))
# Retries after a timed-out attempt (e.g. 1 => two attempts total per URL).
_RESEARCH_FETCH_RETRIES = max(0, int(os.environ.get("RESEARCH_FETCH_RETRIES", "1")))


def _term_preview(term: str, max_len: int = 160) -> str:
    t = term.replace("\n", " ").strip()
    return t if len(t) <= max_len else t[: max_len - 3] + "..."


def _fetch_attempts_total() -> int:
    return 1 + _RESEARCH_FETCH_RETRIES


def _elapsed_ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


def is_likely_google_quota_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    needles = (
        "quota",
        "rate limit",
        "429",
        "resource exhausted",
        "user rate limit",
        "limit exceeded",
        "keys failed",
    )
    return any(n in s for n in needles)


class ResearchPipeline:
    """Owns backend, term generation, and URL fetch adapters; orchestrates research I/O."""

    def __init__(self) -> None:
        self._log = logger.bind(component="ResearchPipeline")
        self.backend = BackendAPIAdapter()
        self.gen_terms = GenSearchTermsPipeline()
        self.fetch_content = FetchUrlContentPipeline()
        self.last_research_breakdown_posts: List[Dict[str, Any]] = []

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
                self._log.info(
                    "research_fetch_retry",
                    event="research",
                    post_id=post_id,
                    url=url,
                    attempt=attempt,
                    attempts_total=attempts,
                    timeout_sec=ts,
                )
            ex = ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(self.fetch_content.fetch, url, use_fetch_cache)
            try:
                out = fut.result(timeout=ts)
            except FutureTimeoutError:
                ex.shutdown(wait=False)
                self._log.warning(
                    "research_fetch_timed_out",
                    event="research",
                    post_id=post_id,
                    url=url,
                    attempt=attempt,
                    attempts_total=attempts,
                    timeout_sec=ts,
                    will_retry=attempt < attempts,
                )
                if attempt >= attempts:
                    err = f"Timed out after {attempts} attempt(s) ({ts}s each)"
                    self._log.error(
                        "research_fetch_timed_out_exhausted",
                        event="research",
                        post_id=post_id,
                        url=url,
                        attempts_total=attempts,
                        timeout_sec=ts,
                    )
                    return FetchUrlResult(url=url, success=False, error=err)
                continue
            except Exception as e:
                ex.shutdown(wait=False)
                self._log.exception(
                    "research_fetch_failed post_id={} url={} attempt={}",
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

    def _web_search_google_or_bing(
        self, query: str, first: int, count: int, post_id: str
    ) -> Dict[str, Any]:
        """Google CSE first; on quota-style failures try Bing (ScrapingDog) if configured."""
        from services.search_service import search_bing

        try:
            return self.backend.google_search(query=query, first=first, count=count)
        except Exception as e:
            if not is_likely_google_quota_error(e):
                raise
            self._log.warning(
                "research_google_bing_fallback",
                event="research",
                post_id=post_id,
                term_preview=_term_preview(query),
            )
            try:
                return search_bing(query=query, first=first, count=count)
            except Exception as e2:
                raise RuntimeError(
                    f"Google search failed for post {post_id} and term {query!r}; "
                    f"Bing fallback failed: {e2}"
                ) from e2

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

        trace_id = str(uuid4())
        log = self._log.bind(trace_id=trace_id)
        t_preview0 = time.perf_counter()
        log.info(
            "research_preview_begin",
            event="research_timing",
            post_id=post_id,
            force=force,
            use_terms_cache=use_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )

        if not force and not self._is_new_post(post):
            preview_total_ms = _elapsed_ms(t_preview0)
            log.info(
                "research_preview_skipped",
                event="research_timing",
                post_id=post_id,
                preview_total_ms=preview_total_ms,
                reason="post_already_researched",
            )
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
                    "timing": {
                        "trace_id": trace_id,
                        "preview_total_ms": preview_total_ms,
                        "skipped": True,
                    },
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
        t_after_terms = time.perf_counter()
        terms_phase_ms = int((t_after_terms - t_preview0) * 1000)
        _dbg_dir = research_debug_log_dir()
        _post_title = post.get("title")
        _post_text = post.get("selftext") or post.get("text")
        if _dbg_dir:
            write_research_terms_debug(
                log_dir=_dbg_dir,
                trace_id=trace_id,
                post_id=post_id,
                search_terms=search_terms,
                terms_report=terms_report,
                post_title=_post_title,
                post_text=_post_text,
            )
        if not search_terms:
            preview_total_ms = _elapsed_ms(t_preview0)
            timing = {
                "trace_id": trace_id,
                "preview_total_ms": preview_total_ms,
                "terms_phase_ms": terms_phase_ms,
                "search_phase_ms": 0,
                "fetch_phase_ms": 0,
            }
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
                "timing": timing,
            }
            log.warning(
                "research_preview_no_terms",
                event="research_timing",
                post_id=post_id,
                preview_total_ms=preview_total_ms,
                terms_phase_ms=terms_phase_ms,
                error=terms_report.get("error"),
            )
            post_copy = dict(post)
            post_copy["search_results"] = []
            return {"post": post_copy, "report": report}

        all_search_results: List[Dict[str, Any]] = []
        search_events: List[Dict[str, Any]] = []
        raw_results_by_term: List[List[Dict[str, Any]]] = []
        seen_links: set[str] = set()
        n_terms = len(search_terms)
        t_search_phase0 = time.perf_counter()
        log.info(
            "research_google_phase_begin",
            event="research",
            post_id=post_id,
            search_term_count=n_terms,
        )

        for idx, term in enumerate(search_terms, start=1):
            t_search = time.perf_counter()
            log.info(
                "research_google_query_begin",
                event="research",
                post_id=post_id,
                term_index=idx,
                term_total=n_terms,
                term_preview=_term_preview(term),
            )
            try:
                search_response = self._web_search_google_or_bing(
                    query=term, first=1, count=10, post_id=str(post_id)
                )
                raw_results = search_response.get("results", [])
                raw_results_by_term.append(list(raw_results))
            except Exception as e:
                log.exception("web search failed post_id={} term={}", post_id, term)
                raise RuntimeError(
                    f"Web search failed for post {post_id} and term '{term}': {e}"
                ) from e
            log.info(
                "research_google_query_done",
                event="research",
                post_id=post_id,
                term_index=idx,
                term_total=n_terms,
                elapsed_ms=_elapsed_ms(t_search),
                raw_hits=len(raw_results),
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

        t_after_search = time.perf_counter()
        search_phase_ms = int((t_after_search - t_search_phase0) * 1000)
        log.info(
            "research_google_phase_done",
            event="research_timing",
            post_id=post_id,
            elapsed_ms=search_phase_ms,
            search_term_count=n_terms,
            unique_links_selected=len(all_search_results),
        )
        if _dbg_dir:
            _corpus = f"{_post_title or ''}\n{_post_text or ''}"
            write_research_results_debug(
                log_dir=_dbg_dir,
                trace_id=trace_id,
                post_id=post_id,
                search_terms=search_terms,
                raw_results_by_term=raw_results_by_term,
                corpus_tokens=tokenize(_corpus),
                raw_hits_total=sum(len(x) for x in raw_results_by_term),
                selected_unique_urls=len(all_search_results),
            )

        fetched_texts: List[str] = []
        fetched_pages: List[Dict[str, Any]] = []
        batch_size = 3
        total_urls = len(all_search_results)
        n_batches = (total_urls + batch_size - 1) // batch_size if total_urls else 0
        t_fetch_phase0 = time.perf_counter()
        log.info(
            "research_url_fetch_phase_begin",
            event="research",
            post_id=post_id,
            unique_result_links=len(all_search_results),
            urls_to_fetch=total_urls,
            batch_size=batch_size,
            batch_count=n_batches,
            fetch_timeout_sec=_RESEARCH_FETCH_TIMEOUT_SEC,
            fetch_retries=_RESEARCH_FETCH_RETRIES,
            fetch_attempts_total=_fetch_attempts_total(),
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
            log.info(
                "research_fetch_batch_begin",
                event="research",
                post_id=post_id,
                batch_index=batch_num,
                batch_url_count=len(urls),
                urls=urls[:5],
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
                        log.exception("content fetch failed post_id={} url={}", post_id, url)
                        fetched_pages.append(
                            {
                                "url": url,
                                "success": False,
                                "error": str(e),
                                "use_cache": use_fetch_cache,
                            }
                        )
            log.info(
                "research_fetch_batch_done",
                event="research",
                post_id=post_id,
                batch_index=batch_num,
                elapsed_ms=_elapsed_ms(t_batch),
            )

        t_after_fetch = time.perf_counter()
        fetch_phase_ms = int((t_after_fetch - t_fetch_phase0) * 1000)
        preview_total_ms = _elapsed_ms(t_preview0)
        log.info(
            "research_fetch_phase_done",
            event="research_timing",
            post_id=post_id,
            elapsed_ms=fetch_phase_ms,
            urls_to_fetch=total_urls,
            batch_count=n_batches,
            pages_recorded=len(fetched_pages),
        )

        post_copy = dict(post)
        post_copy["search_results"] = fetched_texts
        timing = {
            "trace_id": trace_id,
            "preview_total_ms": preview_total_ms,
            "terms_phase_ms": terms_phase_ms,
            "search_phase_ms": search_phase_ms,
            "fetch_phase_ms": fetch_phase_ms,
        }
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
            "timing": timing,
        }
        log.info(
            "research_preview_complete",
            event="research_timing",
            post_id=post_id,
            preview_total_ms=preview_total_ms,
            terms_phase_ms=terms_phase_ms,
            search_phase_ms=search_phase_ms,
            fetch_phase_ms=fetch_phase_ms,
            search_term_count=len(search_terms),
            selected_links=len(all_search_results),
            fetched_texts=len(fetched_texts),
            search_results_hash=report["search_results_hash"],
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
    
    def _research_post_pair(
        self,
        post: Dict,
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        preview = self.preview_post(
            post=post,
            step=step,
            force=force,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )
        return preview["post"], preview["report"]

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
        post_out, _ = self._research_post_pair(
            post,
            step,
            force=force,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
        )
        return post_out
    
    def process_posts(
        self,
        step: str = "filter-researched",
        count: int = 1,
        offset: int = 1,
        include_breakdown: bool = False,
    ) -> List[Dict]:
        """
        Process multiple posts for research.

        Args:
            step: Workflow step name
            count: Number of posts to process
            offset: Offset for pagination
            include_breakdown: When True, populate ``last_research_breakdown_posts``.

        Returns:
            List of researched post dictionaries
        """
        if include_breakdown:
            self.last_research_breakdown_posts = []
        trace_id = str(uuid4())
        log = self._log.bind(trace_id=trace_id)
        t_batch0 = time.perf_counter()
        log.info(
            "research_process_posts_begin",
            event="research_timing",
            step=step,
            count=count,
            offset=offset,
            include_breakdown=include_breakdown,
        )
        posts_list = self.backend.posts_list(step=step, count=count, offset=offset)
        file_names = posts_list.get("fileNames", [])

        if not file_names:
            log.info(
                "research_process_posts_complete",
                event="research_timing",
                elapsed_ms=_elapsed_ms(t_batch0),
                post_count=0,
                reason="no_file_names",
            )
            return []

        posts: List[Dict[str, Any]] = []
        for file_name in file_names:
            try:
                posts.append(self.backend.get_post_local(file_name, step))
            except Exception:
                self._log.exception("research load failed for file={}", file_name)
        results = self.process_post_objects(
            posts=posts, step=step, include_breakdown=include_breakdown
        )
        log.info(
            "research_process_posts_complete",
            event="research_timing",
            elapsed_ms=_elapsed_ms(t_batch0),
            post_count=len(results),
            step=step,
        )
        return results

    def process_post_objects(
        self,
        posts: List[Dict[str, Any]],
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        include_breakdown: bool = False,
    ) -> List[Dict[str, Any]]:
        """Process already-loaded post objects and persist researched versions."""
        researched_posts: List[Dict[str, Any]] = []
        for post in posts:
            post_id = post.get("id", "<unknown>")
            trace_id = str(uuid4())
            log = self._log.bind(trace_id=trace_id)
            t_one = time.perf_counter()
            try:
                was_new = self._is_new_post(post)
                researched, report = self._research_post_pair(
                    post,
                    step,
                    force=force,
                    use_terms_cache=use_terms_cache,
                    persist_terms_cache=persist_terms_cache,
                    use_fetch_cache=use_fetch_cache,
                )
                if include_breakdown:
                    self.last_research_breakdown_posts.append(
                        {"post_id": str(post_id), "report": report}
                    )
                self.backend.save_post_local(researched, step=step)
                if was_new:
                    try:
                        self.backend.save_post(researched, step=step)
                    except Exception:
                        self._log.exception(
                            "research backend save failed for post_id={}", post_id
                        )
                researched_posts.append(researched)
                log.info(
                    "research_post_object_complete",
                    event="research_timing",
                    post_id=post_id,
                    elapsed_ms=_elapsed_ms(t_one),
                    was_new=was_new,
                    step=step,
                )
            except Exception as e:
                log.exception(
                    "research_post_object_failed post_id={} elapsed_ms={}",
                    post_id,
                    _elapsed_ms(t_one),
                    event="research_timing",
                )
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
