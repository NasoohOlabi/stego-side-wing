"""Generate search terms from post content."""
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.utils.protocol_utils import stable_hash, unique_preserve_order
from workflows.utils.text_utils import parse_json_array_response
from workflows.utils.workflow_llm_prompts import format_gen_search_terms_user_prompt, get_prompts

logger = logging.getLogger(__name__)


class GenSearchTermsPipeline:
    """Pipeline for generating search terms from posts."""
    
    def __init__(self):
        self.config = get_config()
        self.llm = LLMAdapter()
        self._init_cache_db()
    
    def _init_cache_db(self):
        """Initialize SQLite cache database (replacing n8n datatable)."""
        cache_db = self.config.research_terms_db_path
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
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.execute(
            "SELECT search_terms FROM research_terms WHERE post_id = ?", (post_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return None
        return None
    
    def _cache_terms(self, post_id: str, terms: List[str]) -> None:
        """Cache search terms for a post."""
        conn = sqlite3.connect(self.cache_db_path)
        conn.execute(
            "INSERT OR REPLACE INTO research_terms (post_id, search_terms) VALUES (?, ?)",
            (post_id, json.dumps(terms)),
        )
        conn.commit()
        conn.close()

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
        prompt = self._build_prompt(
            post_title=post_title,
            post_text=post_text,
            post_url=post_url,
        )
        system_message = get_prompts().gen_search_terms.system_template

        cached_terms: Optional[List[str]] = self._get_cached_terms(post_id) if use_cache else None
        if cached_terms:
            normalized_cached_terms = self._normalize_terms(cached_terms)
            logger.info(
                "search terms cache hit post_id=%s count=%s hash=%s",
                post_id,
                len(normalized_cached_terms),
                stable_hash(normalized_cached_terms),
            )
            return {
                "post_id": post_id,
                "provider": "lm_studio",
                "model": self.config.model,
                "temperature": 0.0,
                "used_cache": True,
                "prompt_hash": stable_hash(prompt),
                "system_prompt_hash": stable_hash(system_message),
                "terms": normalized_cached_terms,
                "terms_hash": stable_hash(normalized_cached_terms),
            }

        try:
            logger.info(
                "gen_search_terms_llm_begin",
                extra={
                    "event": "gen_search_terms",
                    "post_id": post_id,
                    "provider": "lm_studio",
                    "model": self.config.model,
                },
            )
            t_llm = time.perf_counter()
            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=self.config.model,
                provider="lm_studio",
                temperature=0.0,
            )
            llm_ms = int((time.perf_counter() - t_llm) * 1000)
            terms = self._normalize_terms(self._parse_terms(response))
            if terms and persist_cache:
                self._cache_terms(post_id, terms)
            logger.info(
                "generated search terms post_id=%s count=%s hash=%s use_cache=%s persist_cache=%s llm_elapsed_ms=%s",
                post_id,
                len(terms),
                stable_hash(terms),
                use_cache,
                persist_cache,
                llm_ms,
            )
            return {
                "post_id": post_id,
                "provider": "lm_studio",
                "model": self.config.model,
                "temperature": 0.0,
                "used_cache": False,
                "prompt_hash": stable_hash(prompt),
                "system_prompt_hash": stable_hash(system_message),
                "terms": terms,
                "terms_hash": stable_hash(terms),
            }
        except Exception as e:
            logger.exception("search term generation failed for post_id=%s", post_id)
            return {
                "post_id": post_id,
                "provider": "lm_studio",
                "model": self.config.model,
                "temperature": 0.0,
                "used_cache": False,
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
        
        return terms[:20]  # Limit to 20 terms
