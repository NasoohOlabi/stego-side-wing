"""DataLoad pipeline: fetch URL content for unresolved posts."""
from typing import Dict, List

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.contracts import PostPayload
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline


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
                    print(f"Error processing post {file_name}: {e}")
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
                        print(f"Error saving post {post_id}: {e}")
        
        return processed_posts
