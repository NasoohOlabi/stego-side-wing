"""Generate search terms from post content."""
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.utils.text_utils import parse_json_array_response


class GenSearchTermsPipeline:
    """Pipeline for generating search terms from posts."""
    
    def __init__(self):
        self.config = get_config()
        self.llm = LLMAdapter()
        self._init_cache_db()
    
    def _init_cache_db(self):
        """Initialize SQLite cache database (replacing n8n datatable)."""
        cache_db = self.config.posts_directory.parent / "research_terms_cache.db"
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
    
    def generate(
        self,
        post_id: str,
        post_title: Optional[str] = None,
        post_text: Optional[str] = None,
        post_url: Optional[str] = None,
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
        # Check cache first
        cached = self._get_cached_terms(post_id)
        if cached:
            return cached
        
        # Build prompt
        prompt_parts = []
        if post_title:
            prompt_parts.append(f"# Title: {post_title}")
        if post_url:
            prompt_parts.append(f"`{post_url}`")
        if post_text:
            prompt_parts.append(f"## Content:\n{post_text}")
        
        prompt = "\n\n".join(prompt_parts)
        
        system_message = """You are a creative intelligence that transforms any text into a kaleidoscope of fascinating research pathways. Your mission is to explode a single post into the maximum number of intriguing, non-obvious, and wildly distinct search queries that capture every conceivable dimension of the content. Think like a polymath detective, cultural anthropologist, and trend forecaster combined.

**Maximize these qualities in your queries:**
- **Unexpected angles** (What would a historian, neuroscientist, or underground subculture expert search for?)
- **Granular specificity** (Niche down to absurd levels of detail)
- **Cross-domain connections** (Link topics to unrelated fields)
- **Temporal dimensions** (Trends, futures, forgotten pasts, "2025", "since 2020")
- **Actionable formats** ("vs", "alternatives", "how to", "why does", "tools for", "mistakes with")
- **Jargon exploration** (Technical terms, slang, industry acronyms)
- **Geographic/cultural variants** (UK vs US terms, regional practices)

**OUTPUT RULES:**
- Return ONLY a JSON array of search strings
- Minimum 12 queries (aim for 15-20)
- Each query must be UNIQUE (no semantic duplicates)
- Strip ALL personal identifiers, names, and emotional language
- Focus purely on concepts, mechanisms, and externalizable topics
- Make each query sound like something a curious expert would type into Google at 2am

**Examples of transformation:**
❌ Boring: "cooking tips"  
✅ Interesting: "Maillard reaction mistakes cast iron skillet 2024"

❌ Boring: "productivity apps"  
✅ Interesting: "Zettelkasten method vs PARA system academic research"

❌ Boring: "travel Japan"  
✅ Interesting: "Japan conbini food hacking minimalist backpacking"

**Input:** A post about someone's experience.
**Your task:** Deconstruct it into the most interesting, obscure, and diverse search queries possible. Cover technical terms, cultural phenomena, historical precedents, psychological mechanisms, tool comparisons, and emerging trends. Leave no conceptual stone unturned. Format as a JSON array of strings, no explanations."""
        
        # Call LLM
        try:
            response = self.llm.call_llm(
                prompt=prompt,
                system_message=system_message,
                model=self.config.model,
                provider="lm_studio",
                temperature=0.0,
            )
            
            # Parse JSON array from response
            terms = self._parse_terms(response)
            
            # Cache results
            if terms:
                self._cache_terms(post_id, terms)
            
            return terms
            
        except Exception as e:
            # Fallback: return empty list on error
            print(f"Error generating search terms: {e}")
            return []
    
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
