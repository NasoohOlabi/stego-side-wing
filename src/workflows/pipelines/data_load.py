"""DataLoad pipeline: fetch URL content for unresolved posts."""
import logging
from typing import Any, Dict, List

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.contracts import PostPayload
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline
from workflows.utils.protocol_utils import stable_hash, text_preview

logger = logging.getLogger(__name__)


class DataLoadPipeline:
    """Pipeline for loading URL content for posts."""
    
    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.fetch_pipeline = FetchUrlContentPipeline()
    
    def process_posts(
        self,
        step: str = "filter-url-unresolved",
        batch_size: int = 5,
        count: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """
        Process posts by fetching URL content.
        
        Args:
            step: Workflow step name
            batch_size: Batch size for processing
            count: Number of posts to process
            offset: Offset for pagination
        
        Returns:
            List of processed post dictionaries
        """
        # Get list of post filenames
        posts_list = self.backend.posts_list(step=step, count=count, offset=offset)
        file_names = posts_list.get("fileNames", [])
        
        if not file_names:
            return []
        
        processed_posts = []
        
        # Process in batches
        for i in range(0, len(file_names), batch_size):
            batch = file_names[i : i + batch_size]
            batch_results = []
            
            for file_name in batch:
                try:
                    # Get post
                    post = self.backend.get_post_local(file_name, step)
                    
                    # Extract URL
                    url = post.get("url")
                    if not url:
                        continue
                    
                    # Fetch URL content
                    fetch_result = self.fetch_pipeline.fetch(url, use_cache=True)
                    
                    # Merge result into post
                    if fetch_result.success and fetch_result.text:
                        post["selftext"] = fetch_result.text
                        batch_results.append(post)
                    
                except Exception as e:
                    logger.exception("data_load failed for file=%s step=%s", file_name, step)
                    continue
            
            # Save processed posts
            for post in batch_results:
                post_id = post.get("id")
                if not post_id:
                    continue
                
                # Only save if selftext is present
                if post.get("selftext") and post["selftext"].strip():
                    try:
                        self.backend.save_post_local(post, step="filter-url-unresolved")
                        processed_posts.append(post)
                    except Exception as e:
                        logger.exception("data_load save failed for post_id=%s", post_id)
        
        return processed_posts

    def preview_post(
        self,
        post: Dict,
        *,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """Fetch URL content for an in-memory post dict (receiver / object workflows)."""
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must have 'id' field")
        url = post.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Post {post_id} does not contain a valid 'url'")

        fetch_result = self.fetch_pipeline.fetch(url.strip(), use_cache=use_cache)
        report = {
            "post_id": str(post_id),
            "step": None,
            "url": url.strip(),
            "use_cache": use_cache,
            "fetch_success": fetch_result.success,
            "content_type": fetch_result.content_type,
            "error": fetch_result.error,
        }
        post_copy = dict(post)
        if not fetch_result.success or not fetch_result.text:
            logger.warning(
                "data_load preview_post failed post_id=%s use_cache=%s error=%s",
                post_id,
                use_cache,
                fetch_result.error,
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
        logger.info(
            "data_load preview_post post_id=%s use_cache=%s selftext_hash=%s length=%s",
            post_id,
            use_cache,
            report["selftext_hash"],
            report["selftext_length"],
        )
        return {"post": post_copy, "report": report}

    def preview_post_id(
        self,
        post_id: str,
        step: str = "filter-url-unresolved",
        use_cache: bool = True,
    ) -> Dict:
        """Fetch one post live without mutating step artifacts."""
        file_name = f"{post_id}.json"
        post = self.backend.get_post_local(file_name, step)
        url = post.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Post {post_id} does not contain a valid 'url'")

        fetch_result = self.fetch_pipeline.fetch(url.strip(), use_cache=use_cache)
        report = {
            "post_id": post_id,
            "step": step,
            "url": url.strip(),
            "use_cache": use_cache,
            "fetch_success": fetch_result.success,
            "content_type": fetch_result.content_type,
            "error": fetch_result.error,
        }
        if not fetch_result.success or not fetch_result.text:
            logger.warning(
                "data_load preview failed post_id=%s use_cache=%s error=%s",
                post_id,
                use_cache,
                fetch_result.error,
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
        logger.info(
            "data_load preview post_id=%s use_cache=%s selftext_hash=%s length=%s",
            post_id,
            use_cache,
            report["selftext_hash"],
            report["selftext_length"],
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
        preview = self.preview_post_id(post_id=post_id, step=step, use_cache=use_cache)
        post = preview["post"]
        report = preview["report"]
        if not report.get("fetch_success") or not post.get("selftext"):
            raise RuntimeError(
                f"Failed to fetch URL content for post {post_id}: {report.get('error')}"
            )
        self.backend.save_post_local(post, step=step)
        return post
