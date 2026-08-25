"""Research pipeline: generate search terms, search, and fetch content."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any
from uuid import uuid4

from loguru import logger

from infrastructure.config import (
    get_workflow_capacity_profile,
    get_workflow_research_fetch_concurrency,
    get_workflow_research_fetch_retries,
    get_workflow_research_fetch_timeout_sec,
    get_workflow_research_max_selected_urls,
)
from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.contracts import FetchUrlResult
from workflows.errors import QuotaExceededError
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline
from workflows.utils.protocol_utils import stable_hash, text_preview
from workflows.utils.research_relevance_debug import (
    research_debug_log_dir,
    tokenize,
    write_research_results_debug,
    write_research_terms_debug,
)


def _term_preview(term: str, max_len: int = 160) -> str:
    t = term.replace("\n", " ").strip()
    return t if len(t) <= max_len else t[: max_len - 3] + "..."


def _fetch_attempts_total() -> int:
    return 1 + get_workflow_research_fetch_retries()


def _elapsed_ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


def _fetch_progress_fields(urls_completed: int, urls_total: int) -> dict[str, float | int]:
    """Share of URL fetches finished (by count), for batch/phase logs."""
    if urls_total <= 0:
        return {"urls_total": 0, "urls_completed": 0, "fetch_progress_pct": 100.0}
    c = max(0, min(urls_completed, urls_total))
    return {
        "urls_total": urls_total,
        "urls_completed": c,
        "fetch_progress_pct": round(100.0 * c / urls_total, 2),
    }


def _fetch_url_slot_progress(url_index: int, urls_total: int) -> dict[str, float | int]:
    """Queue slot before this URL (1-based index); parallel fetches may finish out of order."""
    if urls_total <= 0:
        return {}
    before = max(0, url_index - 1)
    return {
        "urls_total": urls_total,
        "url_index": url_index,
        "fetch_progress_pct": round(100.0 * before / urls_total, 2),
    }


def is_likely_google_quota_error(exc: BaseException) -> bool:
    """True for provider quota/rate-limit failures.

    Prefers the typed error; the substring scan remains because the providers themselves
    raise their own exception classes and only say so in the message.
    """
    if isinstance(exc, QuotaExceededError):
        return True
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

    def __init__(
        self,
        *,
        backend: BackendAPIAdapter | None = None,
        gen_terms: GenSearchTermsPipeline | None = None,
        fetch_content: FetchUrlContentPipeline | None = None,
    ) -> None:
        self._log = logger.bind(component="ResearchPipeline")
        self.backend = backend or BackendAPIAdapter()
        self.gen_terms = gen_terms or GenSearchTermsPipeline()
        self.fetch_content = fetch_content or FetchUrlContentPipeline()
        self.last_research_breakdown_posts: list[dict[str, Any]] = []
        self._google_quota_detected = False

    def _fetch_url_with_timeout_retries(
        self,
        post_id: str,
        url: str,
        use_fetch_cache: bool,
        url_index: int | None = None,
        urls_total: int | None = None,
    ) -> FetchUrlResult:
        """Run fetch in an isolated worker with per-attempt timeout; retry on timeout."""
        attempts = _fetch_attempts_total()
        ts = get_workflow_research_fetch_timeout_sec()
        prog: dict[str, float | int] = {}
        if url_index is not None and urls_total is not None:
            prog = _fetch_url_slot_progress(url_index, urls_total)
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
                    **prog,
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
                    **prog,
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
                        **prog,
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
                    **prog,
                )
                return FetchUrlResult(url=url, success=False, error=str(e))
            else:
                ex.shutdown(wait=True)
                return out
        raise RuntimeError("research fetch retry loop fell through")

    @staticmethod
    def _search_summary(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": result.get("title", ""),
            "link": result.get("link", ""),
            "snippet": result.get("snippet", ""),
            "snippet_hash": stable_hash(result.get("snippet", "")),
        }

    def _try_fallback_search(
        self,
        *,
        provider: str,
        search_fn: Any,
        query: str,
        first: int,
        count: int,
        post_id: str,
    ) -> dict[str, Any] | None:
        """Run one fallback provider; return results dict or None when empty/unusable."""
        self._log.warning(
            "research_search_fallback",
            event="research",
            provider=provider,
            post_id=post_id,
            term_preview=_term_preview(query),
        )
        try:
            payload = search_fn(query=query, first=first, count=count)
        except Exception as exc:
            self._log.warning(
                "research_search_fallback_failed",
                event="research",
                provider=provider,
                post_id=post_id,
                error=str(exc)[:300],
            )
            return None
        results = payload.get("results") if isinstance(payload, dict) else None
        if isinstance(results, list):
            relevant = self._relevant_fallback_results(query, results)
            if relevant:
                return {**payload, "results": relevant}
        return None

    @staticmethod
    def _relevant_fallback_results(
        query: str, results: list[Any]
    ) -> list[dict[str, Any]]:
        """Reject quota-fallback hits with little lexical connection to the term."""
        ignored = {
            "about", "analysis", "core", "details", "elements", "from", "into",
            "key", "more", "news", "study", "than", "that", "their", "this",
            "topic", "versus", "what", "when", "where", "with",
        }
        tokens = {
            token for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) >= 4 and token not in ignored
        }
        if not tokens:
            return []
        threshold = min(2, len(tokens))
        relevant: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            haystack = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
            if sum(token in haystack for token in tokens) >= threshold:
                relevant.append(item)
        return relevant

    def _web_search_google_or_bing(
        self,
        query: str,
        first: int,
        count: int,
        post_id: str,
        *,
        disable_bing_fallback: bool = False,
    ) -> dict[str, Any]:
        """Search with free fallbacks before the metered Bing provider."""
        from services.search_service import (
            search_bing,
            search_bing_news_rss,
            search_duckduckgo,
            search_google_news_rss,
            search_yahoo_news,
        )
        from workflows.errors import QuotaExceededError

        google_error: Exception = QuotaExceededError("Google quota circuit is open")
        if not getattr(self, "_google_quota_detected", False):
            try:
                return self.backend.google_search(query=query, first=first, count=count)
            except Exception as exc:
                if not is_likely_google_quota_error(exc):
                    raise
                self._google_quota_detected = True
                google_error = exc
        if disable_bing_fallback:
            raise google_error
        for provider, fn in (
            ("duckduckgo", search_duckduckgo),
            ("yahoo_news", search_yahoo_news),
            ("google_news_rss", search_google_news_rss),
            ("bing_news_rss", search_bing_news_rss),
            ("bing", search_bing),
        ):
            hit = self._try_fallback_search(
                provider=provider,
                search_fn=fn,
                query=query,
                first=first,
                count=count,
                post_id=post_id,
            )
            if hit is not None:
                return hit
        raise QuotaExceededError(
            f"Google search quota exhausted for post {post_id} and term {query!r}; "
            "DuckDuckGo, news RSS, and Bing API fallbacks returned no usable results"
        ) from google_error

    def preview_post(
        self,
        post: dict[str, Any],
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        disable_bing_fallback: bool = False,
    ) -> dict[str, Any]:
        """Run the live research protocol without saving the output."""
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must have 'id' field")

        trace_id = str(uuid4())
        log = self._log.bind(trace_id=trace_id)
        t_preview0 = time.perf_counter()
        max_selected_urls = get_workflow_research_max_selected_urls()
        capacity_profile = get_workflow_capacity_profile()
        log.info(
            "research_preview_begin",
            event="research_timing",
            post_id=post_id,
            force=force,
            use_terms_cache=use_terms_cache,
            use_fetch_cache=use_fetch_cache,
            capacity_profile=capacity_profile,
            max_selected_urls=max_selected_urls,
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
            fallback_query = str(post.get("title") or "").strip()
            if fallback_query:
                search_terms = [fallback_query]
                terms_report = {
                    **terms_report,
                    "terms": search_terms,
                    "fallback": "post_title",
                }
                log.warning(
                    "research_terms_title_fallback",
                    event="research_timing",
                    post_id=post_id,
                    terms_phase_ms=terms_phase_ms,
                    error=terms_report.get("error"),
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

        all_search_results: list[dict[str, Any]] = []
        search_events: list[dict[str, Any]] = []
        raw_results_by_term: list[list[dict[str, Any]]] = []
        seen_links: set[str] = set()
        selected_url_cap_hit = False
        n_terms = len(search_terms)
        t_search_phase0 = time.perf_counter()
        log.info(
            "research_google_phase_begin",
            event="research",
            post_id=post_id,
            search_term_count=n_terms,
            capacity_profile=capacity_profile,
            max_selected_urls=max_selected_urls,
            terms_capped=terms_report.get("terms_capped", False),
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
                    query=term,
                    first=1,
                    count=10,
                    post_id=str(post_id),
                    disable_bing_fallback=disable_bing_fallback,
                )
                raw_results = search_response.get("results", [])
                raw_results_by_term.append(list(raw_results))
            except QuotaExceededError as e:
                log.warning(
                    "research_term_skipped_no_search_results",
                    event="research",
                    post_id=post_id,
                    term_preview=_term_preview(term),
                    error=str(e)[:300],
                )
                raw_results = []
                raw_results_by_term.append([])
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

            selected_for_term: list[dict[str, Any]] = []
            skipped_for_term: list[dict[str, Any]] = []
            for result in raw_results:
                if max_selected_urls == 0:
                    selected_url_cap_hit = True
                    skipped_for_term.append(
                        {
                            "reason": "capacity_url_cap_reached",
                            "max_selected_urls": max_selected_urls,
                        }
                    )
                    break
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
                if max_selected_urls > 0 and len(all_search_results) >= max_selected_urls:
                    selected_url_cap_hit = True
                    skipped_for_term.append(
                        {
                            "reason": "capacity_url_cap_reached",
                            "max_selected_urls": max_selected_urls,
                        }
                    )
                    break

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
            if selected_url_cap_hit:
                break

        t_after_search = time.perf_counter()
        search_phase_ms = int((t_after_search - t_search_phase0) * 1000)
        log.info(
            "research_google_phase_done",
            event="research_timing",
            post_id=post_id,
            elapsed_ms=search_phase_ms,
            search_term_count=n_terms,
            unique_links_selected=len(all_search_results),
            selected_url_cap_hit=selected_url_cap_hit,
            max_selected_urls=max_selected_urls,
        )
        if not all_search_results:
            raise RuntimeError(f"No relevant search results found for post {post_id}")
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

        fetched_texts: list[str] = []
        fetched_pages: list[dict[str, Any]] = []
        batch_size = get_workflow_research_fetch_concurrency()
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
            fetch_timeout_sec=get_workflow_research_fetch_timeout_sec(),
            fetch_retries=get_workflow_research_fetch_retries(),
            fetch_attempts_total=_fetch_attempts_total(),
            **_fetch_progress_fields(0, total_urls),
        )

        batch_num = 0
        for i in range(0, len(all_search_results), batch_size):
            batch = all_search_results[i : i + batch_size]
            urls: list[str] = [str(link) for link in (r.get("link") for r in batch) if link]
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
                **_fetch_progress_fields(i, total_urls),
            )
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                future_items = [
                    (
                        url,
                        i + j + 1,
                        pool.submit(
                            self._fetch_url_with_timeout_retries,
                            post_id,
                            url,
                            use_fetch_cache,
                            i + j + 1,
                            total_urls,
                        ),
                    )
                    for j, url in enumerate(urls)
                ]
                for url, url_index, future in future_items:
                    try:
                        fetch_result = future.result()
                        page_report: dict[str, Any] = {
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
                        log.exception(
                            "content fetch failed post_id={} url={}",
                            post_id,
                            url,
                            **_fetch_url_slot_progress(url_index, total_urls),
                        )
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
                **_fetch_progress_fields(i + len(urls), total_urls),
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
            **_fetch_progress_fields(total_urls, total_urls),
        )
        if not fetched_texts:
            raise RuntimeError(f"No usable research pages fetched for post {post_id}")

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
            "selected_results": [self._search_summary(result) for result in all_search_results],
            "fetched_pages": fetched_pages,
            "search_results": fetched_texts,
            "search_results_hash": stable_hash(fetched_texts),
            "search_results_count": len(fetched_texts),
            "capacity": {
                "profile": capacity_profile,
                "max_selected_urls": max_selected_urls,
                "selected_url_cap_hit": selected_url_cap_hit,
                "terms_capped": bool(terms_report.get("terms_capped", False)),
                "max_terms": terms_report.get("max_terms"),
            },
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
            selected_url_cap_hit=selected_url_cap_hit,
            max_selected_urls=max_selected_urls,
        )
        return {"post": post_copy, "report": report}

    @staticmethod
    def _is_new_post(post: dict[str, Any]) -> bool:
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
            flattened: list[Any] = []
            for value in search_results.values():
                if isinstance(value, list):
                    flattened.extend(value)
                else:
                    flattened.append(value)
            return len([x for x in flattened if isinstance(x, str) and x.strip()]) == 0

        return False

    def _research_post_pair(
        self,
        post: dict,
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        disable_bing_fallback: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        preview = self.preview_post(
            post=post,
            step=step,
            force=force,
            use_terms_cache=use_terms_cache,
            persist_terms_cache=persist_terms_cache,
            use_fetch_cache=use_fetch_cache,
            disable_bing_fallback=disable_bing_fallback,
        )
        return preview["post"], preview["report"]

    def research_post(
        self,
        post: dict,
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        disable_bing_fallback: bool = False,
    ) -> dict:
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
            disable_bing_fallback=disable_bing_fallback,
        )
        return post_out

    def process_posts(
        self,
        step: str = "filter-researched",
        count: int = 1,
        offset: int = 1,
        include_breakdown: bool = False,
        disable_bing_fallback: bool = False,
    ) -> list[dict]:
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

        posts: list[dict[str, Any]] = []
        for file_name in file_names:
            try:
                posts.append(self.backend.get_post_local(file_name, step))
            except Exception:
                self._log.exception("research load failed for file={}", file_name)
        results = self.process_post_objects(
            posts=posts,
            step=step,
            include_breakdown=include_breakdown,
            disable_bing_fallback=disable_bing_fallback,
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
        posts: list[dict[str, Any]],
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        include_breakdown: bool = False,
        disable_bing_fallback: bool = False,
    ) -> list[dict[str, Any]]:
        """Process already-loaded post objects and persist researched versions."""
        researched_posts: list[dict[str, Any]] = []
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
                    disable_bing_fallback=disable_bing_fallback,
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
                        self._log.exception("research backend save failed for post_id={}", post_id)
                researched_posts.append(researched)
                log.info(
                    "research_post_object_complete",
                    event="research_timing",
                    post_id=post_id,
                    elapsed_ms=_elapsed_ms(t_one),
                    was_new=was_new,
                    step=step,
                )
            except Exception:
                log.exception(
                    "research_post_object_failed post_id={} elapsed_ms={}",
                    post_id,
                    _elapsed_ms(t_one),
                    event="research_timing",
                )
        return researched_posts

    def process_post_id(
        self,
        post_id: str,
        step: str = "filter-researched",
        force: bool = False,
        use_terms_cache: bool = True,
        persist_terms_cache: bool = True,
        use_fetch_cache: bool = True,
        disable_bing_fallback: bool = False,
    ) -> dict[str, Any]:
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
            disable_bing_fallback=disable_bing_fallback,
        )
        if not results:
            raise RuntimeError(f"Research returned no result for post {post_id}")
        return results[0]
