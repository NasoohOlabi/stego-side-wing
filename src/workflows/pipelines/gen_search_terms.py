"""Generate search terms from post content."""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from infrastructure.config import resolve_workflow_llm_provider_and_model
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.utils.debug_probe import write_debug_probe
from workflows.utils.protocol_utils import stable_hash, unique_preserve_order
from workflows.utils.text_utils import parse_json_array_response
from workflows.utils.workflow_llm_prompts import format_gen_search_terms_user_prompt, get_prompts


class GenSearchTermsPipeline:
    """Generates and SQLite-caches LLM search terms per post for research."""

    def __init__(self) -> None:
        self.config = get_config()
        self.llm = LLMAdapter()
        self._last_cache_error: Optional[str] = None
        self._last_parse_mode: Optional[str] = None
        self._log = logger.bind(component="GenSearchTermsPipeline")
        self._init_cache_db()
    
    def _sync_research_terms_cache_binding(self) -> None:
        """Rebind terms DB to current :func:`get_config` (e.g. under isolated workflow config)."""
        self.config = get_config()
        new_path = self.config.research_terms_db_path
        if getattr(self, "cache_db_path", None) != new_path:
            self._init_cache_db()

    def _init_cache_db(self):
        """Initialize SQLite cache database (replacing n8n datatable)."""
        cache_db = self.config.research_terms_db_path
        Path(cache_db).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(cache_db)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_terms (
                post_id TEXT PRIMARY KEY,
                search_terms TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()
        self.cache_db_path = cache_db
    
    def _get_cached_terms(self, post_id: str) -> Optional[List[str]]:
        """Get cached search terms for a post."""
        if not hasattr(self, "_log"):
            object.__setattr__(
                self, "_log", logger.bind(component="GenSearchTermsPipeline")
            )
        self._last_cache_error = None
        try:
            conn = sqlite3.connect(self.cache_db_path)
            try:
                cursor = conn.execute(
                    "SELECT search_terms FROM research_terms WHERE post_id = ?", (post_id,)
                )
                row = cursor.fetchone()
            finally:
                conn.close()
        except Exception as exc:
            self._last_cache_error = str(exc)
            self._log.exception(
                "gen_search_terms_cache_read_failed",
                event="gen_search_terms_cache",
                cache_action="read",
                post_id=post_id,
            )
            return None

        if not row:
            return None

        try:
            cached = json.loads(row[0])
            if not isinstance(cached, list):
                self._last_cache_error = "cache root must be array"
                self._log.warning(
                    "gen_search_terms_cache_corrupt",
                    event="gen_search_terms_cache",
                    cache_action="corrupt",
                    post_id=post_id,
                    cache_error=self._last_cache_error,
                )
                return None
            return cached
        except json.JSONDecodeError as exc:
            self._last_cache_error = str(exc)
            self._log.warning(
                "gen_search_terms_cache_corrupt",
                event="gen_search_terms_cache",
                cache_action="corrupt",
                post_id=post_id,
                cache_error=self._last_cache_error,
            )
            return None
    
    def _cache_terms(self, post_id: str, terms: List[str]) -> None:
        """Cache search terms for a post."""
        if not hasattr(self, "_log"):
            object.__setattr__(
                self, "_log", logger.bind(component="GenSearchTermsPipeline")
            )
        try:
            conn = sqlite3.connect(self.cache_db_path)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO research_terms (post_id, search_terms) VALUES (?, ?)",
                    (post_id, json.dumps(terms)),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            self._log.exception(
                "gen_search_terms_cache_write_failed",
                event="gen_search_terms_cache",
                cache_action="write",
                post_id=post_id,
            )

    @staticmethod
    def _build_prompt(
        post_title: Optional[str] = None,
        post_text: Optional[str] = None,
        post_url: Optional[str] = None,
    ) -> str:
        return format_gen_search_terms_user_prompt(
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
        )

    @staticmethod
    def _normalize_terms(terms: List[str]) -> List[str]:
        return unique_preserve_order(str(term) for term in terms if str(term).strip())

    def preview_generation(
        self,
        post_id: str,
        post_title: Optional[str] = None,
        post_text: Optional[str] = None,
        post_url: Optional[str] = None,
        use_cache: bool = True,
        persist_cache: bool = True,
    ) -> Dict[str, Any]:
        """Generate search terms and return protocol metadata."""
        if not hasattr(self, "_log"):
            object.__setattr__(
                self, "_log", logger.bind(component="GenSearchTermsPipeline")
            )
        self._sync_research_terms_cache_binding()
        t_start = time.perf_counter()
        prompt = self._build_prompt(
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
        )
        system_message = get_prompts().gen_search_terms.system_template
        llm_provider, llm_model = resolve_workflow_llm_provider_and_model(
            self.config.model or "mistral-nemo-instruct-2407-abliterated"
        )
        cache_hit = False
        cache_error = None
        cached_terms: Optional[List[str]] = None
        cache_db_path = str(getattr(self, "cache_db_path", "") or "") or None
        # region agent log
        write_debug_probe(
            run_id=None,
            hypothesis_id="H2",
            location="workflows/pipelines/gen_search_terms.py:preview_generation:begin",
            message="search-term preview started",
            data={
                "post_id": post_id,
                "use_cache": use_cache,
                "persist_cache": persist_cache,
                "prompt_hash": stable_hash(prompt),
                "system_prompt_hash": stable_hash(system_message),
            },
        )
        # endregion

        if use_cache:
            cached_terms = self._get_cached_terms(post_id)
            cache_error = getattr(self, "_last_cache_error", None)
            cache_hit = cached_terms is not None
        if cached_terms is not None:
            normalized_cached_terms = self._normalize_terms(cached_terms)
            elapsed_ms = int((time.perf_counter() - t_start) * 1000)
            self._log.info(
                "gen_search_terms_cache_hit",
                event="gen_search_terms",
                post_id=post_id,
                cache_hit=True,
                cache_error=cache_error,
                parse_mode="cache",
                elapsed_ms=elapsed_ms,
                terms_count=len(normalized_cached_terms),
                terms_hash=stable_hash(normalized_cached_terms),
            )
            return {
                "post_id": post_id,
                "provider": llm_provider,
                "model": llm_model,
                "temperature": 0.0,
                "used_cache": True,
                "cache_hit": True,
                "cache_error": cache_error,
                "cache_db_path": cache_db_path,
                "parse_mode": "cache",
                "elapsed_ms": elapsed_ms,
                "retry_count": 0,
                "prompt_hash": stable_hash(prompt),
                "system_prompt_hash": stable_hash(system_message),
                "terms": normalized_cached_terms,
                "terms_hash": stable_hash(normalized_cached_terms),
            }

        try:
            # region agent log
            write_debug_probe(
                run_id=None,
                hypothesis_id="H2",
                location="workflows/pipelines/gen_search_terms.py:preview_generation:llm_begin",
                message="search-term llm call starting",
                data={
                    "post_id": post_id,
                    "provider": llm_provider,
                    "model": llm_model,
                },
            )
            # endregion
            self._log.info(
                "gen_search_terms_llm_begin",
                event="gen_search_terms",
                post_id=post_id,
                provider=llm_provider,
                model=llm_model,
            )
            t_llm = time.perf_counter()
            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=llm_model,
                provider=llm_provider,
                temperature=0.0,
            )
            llm_ms = int((time.perf_counter() - t_llm) * 1000)
            llm_meta = dict(getattr(self.llm, "last_call_metadata", {}) or {})
            terms = self._normalize_terms(self._parse_terms(response))
            if persist_cache:
                self._cache_terms(post_id, terms)
            total_ms = int((time.perf_counter() - t_start) * 1000)
            # region agent log
            write_debug_probe(
                run_id=str(llm_meta.get("run_id") or ""),
                hypothesis_id="H2",
                location="workflows/pipelines/gen_search_terms.py:preview_generation:success",
                message="search-term generation succeeded",
                data={
                    "post_id": post_id,
                    "terms_count": len(terms),
                    "parse_mode": getattr(self, "_last_parse_mode", None),
                    "retry_count": int(llm_meta.get("retry_count", 0) or 0),
                    "elapsed_ms": total_ms,
                },
            )
            # endregion
            self._log.info(
                "gen_search_terms_generated",
                event="gen_search_terms",
                post_id=post_id,
                cache_hit=cache_hit,
                cache_error=cache_error,
                parse_mode=getattr(self, "_last_parse_mode", None),
                elapsed_ms=total_ms,
                llm_elapsed_ms=llm_ms,
                retry_count=int(llm_meta.get("retry_count", 0) or 0),
                terms_count=len(terms),
                terms_hash=stable_hash(terms),
                use_cache=use_cache,
                persist_cache=persist_cache,
            )
            return {
                "post_id": post_id,
                "provider": llm_provider,
                "model": llm_model,
                "temperature": 0.0,
                "used_cache": False,
                "cache_hit": False,
                "cache_error": cache_error,
                "cache_db_path": cache_db_path,
                "parse_mode": getattr(self, "_last_parse_mode", None),
                "elapsed_ms": total_ms,
                "llm_elapsed_ms": llm_ms,
                "retry_count": int(llm_meta.get("retry_count", 0) or 0),
                "prompt_hash": stable_hash(prompt),
                "system_prompt_hash": stable_hash(system_message),
                "terms": terms,
                "terms_hash": stable_hash(terms),
            }
        except Exception as e:
            llm_meta = dict(getattr(self.llm, "last_call_metadata", {}) or {})
            elapsed_ms = int((time.perf_counter() - t_start) * 1000)
            # region agent log
            write_debug_probe(
                run_id=str(llm_meta.get("run_id") or ""),
                hypothesis_id="H2",
                location="workflows/pipelines/gen_search_terms.py:preview_generation:failure",
                message="search-term generation failed",
                data={
                    "post_id": post_id,
                    "cache_hit": cache_hit,
                    "cache_error": cache_error,
                    "parse_mode": getattr(self, "_last_parse_mode", None),
                    "error_kind": type(e).__name__,
                    "http_status": llm_meta.get("http_status"),
                    "retry_count": int(llm_meta.get("retry_count", 0) or 0),
                    "response_snippet": llm_meta.get("response_snippet"),
                },
            )
            # endregion
            self._log.exception(
                "gen_search_terms_generation_failed",
                event="gen_search_terms",
                post_id=post_id,
                cache_hit=cache_hit,
                cache_error=cache_error,
                parse_mode=getattr(self, "_last_parse_mode", None),
                elapsed_ms=elapsed_ms,
                retry_count=int(llm_meta.get("retry_count", 0) or 0),
                http_status=llm_meta.get("http_status"),
                error_kind=llm_meta.get("error_kind") or type(e).__name__,
                response_snippet=llm_meta.get("response_snippet"),
            )
            return {
                "post_id": post_id,
                "provider": llm_provider,
                "model": llm_model,
                "temperature": 0.0,
                "used_cache": False,
                "cache_hit": cache_hit,
                "cache_error": cache_error,
                "cache_db_path": cache_db_path,
                "parse_mode": getattr(self, "_last_parse_mode", None),
                "elapsed_ms": elapsed_ms,
                "retry_count": int(llm_meta.get("retry_count", 0) or 0),
                "http_status": llm_meta.get("http_status"),
                "error_kind": llm_meta.get("error_kind") or type(e).__name__,
                "response_snippet": llm_meta.get("response_snippet"),
                "prompt_hash": stable_hash(prompt),
                "system_prompt_hash": stable_hash(system_message),
                "terms": [],
                "terms_hash": stable_hash([]),
                "error": str(e),
            }
    
    def generate(
        self,
        post_id: str,
        post_title: Optional[str] = None,
        post_text: Optional[str] = None,
        post_url: Optional[str] = None,
        use_cache: bool = True,
        persist_cache: bool = True,
    ) -> List[str]:
        """
        Generate search terms for a post.
        
        Args:
            post_id: Post identifier
            post_title: Post title
            post_text: Post text content
            post_url: Post URL
        
        Returns:
            List of search term strings
        """
        report = self.preview_generation(
            post_id=post_id,
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
            use_cache=use_cache,
            persist_cache=persist_cache,
        )
        return list(report.get("terms", []))
    
    def _parse_terms(self, response: str) -> List[str]:
        """Parse search terms from LLM response."""
        terms = parse_json_array_response(response)
        if terms:
            self._last_parse_mode = "json_array"
            return [str(t) for t in terms if t]
        
        # Last resort: split by newlines and commas
        terms = []
        for line in response.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Remove quotes and brackets
            line = line.strip('"\'[]')
            if line:
                terms.append(line)
        self._last_parse_mode = "line_fallback" if terms else "empty"
        return terms[:20]  # Limit to 20 terms
