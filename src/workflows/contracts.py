"""Type definitions for workflow payloads and state."""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PostPayload:
    """Post data structure used across workflows."""
    id: str
    title: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    post_id: Optional[str] = None
    post_title: Optional[str] = None
    post_text: Optional[str] = None
    post_url: Optional[str] = None
    search_results: Optional[List[Dict[str, Any]]] = None
    angles: Optional[List[Dict[str, Any]]] = None
    options_count: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result: Dict[str, Any] = {}
        if self.id:
            result["id"] = self.id
        if self.title:
            result["title"] = self.title
        if self.text:
            result["text"] = self.text
        if self.url:
            result["url"] = self.url
        if self.post_id:
            result["post_id"] = self.post_id
        if self.post_title:
            result["post_title"] = self.post_title
        if self.post_text:
            result["post_text"] = self.post_text
        if self.post_url:
            result["post_url"] = self.post_url
        if self.search_results:
            result["search_results"] = self.search_results
        if self.angles:
            result["angles"] = self.angles
        if self.options_count is not None:
            result["options_count"] = self.options_count
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PostPayload":
        """Create from dictionary."""
        return cls(
            id=data.get("id", ""),
            title=data.get("title"),
            text=data.get("text"),
            url=data.get("url"),
            post_id=data.get("post_id"),
            post_title=data.get("post_title"),
            post_text=data.get("post_text"),
            post_url=data.get("post_url"),
            search_results=data.get("search_results"),
            angles=data.get("angles"),
            options_count=data.get("options_count"),
        )


@dataclass
class SearchResult:
    """Search result structure."""
    title: str
    link: str
    snippet: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "link": self.link,
            "snippet": self.snippet,
        }


@dataclass
class FetchUrlResult:
    """URL fetch result structure."""
    url: str
    success: bool
    text: Optional[str] = None
    content_type: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "url": self.url,
            "success": self.success,
        }
        if self.text:
            result["text"] = self.text
        if self.content_type:
            result["content_type"] = self.content_type
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class AngleResult:
    """Angle analysis result."""
    source_quote: str
    tangent: str
    category: str
    source_document: Optional[int] = None
    idx: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "source_quote": self.source_quote,
            "tangent": self.tangent,
            "category": self.category,
        }
        if self.source_document is not None:
            result["source_document"] = self.source_document
        if self.idx is not None:
            result["idx"] = self.idx
        return result


@dataclass
class StegoResult:
    """Steganographic encoding result."""
    stego_text: str
    post: Dict[str, Any]
    embedding_metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "stego_text": self.stego_text,
            "post": self.post,
        }
        if self.embedding_metadata:
            result["embedding_metadata"] = self.embedding_metadata
        return result
