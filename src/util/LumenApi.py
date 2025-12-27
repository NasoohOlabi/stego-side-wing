from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Meta:
    total_hits: int
    page: int
    per_page: int
    total_pages: int
    query_time_ms: int


@dataclass
class Article:
    id: str
    title: str
    content_excerpt: str
    full_content_html: Optional[str]
    full_content_sanitized: Optional[str]
    author: Optional[str]
    keywords: List[str]
    language: str
    country: str
    publisher_id: str
    topic_id: str
    sentiment_label: str
    has_video: bool
    published_at: int  # Unix timestamp
    sentiment_score: float
    source_link: str
    image_url: Optional[str]


@dataclass
class ArticlesResponse:
    meta: Meta
    data: List[Article]

    @staticmethod
    def from_dict(obj: Dict[str, Any]) -> "ArticlesResponse":
        meta_dict = obj.get("meta", {})
        meta = Meta(
            total_hits=meta_dict.get("total_hits", 0),
            page=meta_dict.get("page", 1),
            per_page=meta_dict.get("per_page", 20),
            total_pages=meta_dict.get("total_pages", 1),
            query_time_ms=meta_dict.get("query_time_ms", 0),
        )

        articles = []
        for item in obj.get("data", []):
            articles.append(
                Article(
                    id=item.get("id", ""),
                    title=item.get("title", ""),
                    content_excerpt=item.get("content_excerpt", ""),
                    full_content_html=item.get("full_content_html"),
                    full_content_sanitized=item.get("full_content_sanitized"),
                    author=item.get("author"),
                    keywords=item.get("keywords", []),
                    language=item.get("language", ""),
                    country=item.get("country", ""),
                    publisher_id=item.get("publisher_id", ""),
                    topic_id=item.get("topic_id", ""),
                    sentiment_label=item.get("sentiment_label", ""),
                    has_video=item.get("has_video", False),
                    published_at=item.get("published_at", 0),
                    sentiment_score=item.get("sentiment_score", 0.0),
                    source_link=item.get("source_link", ""),
                    image_url=item.get("image_url"),
                )
            )

        return ArticlesResponse(meta=meta, data=articles)
