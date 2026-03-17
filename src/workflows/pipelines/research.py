"""Research pipeline: generate search terms, search, and fetch content."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.pipelines.fetch_url_content import FetchUrlContentPipeline
from workflows.pipelines.gen_search_terms import GenSearchTermsPipeline


class ResearchPipeline:
    """Pipeline for researching posts."""
    
    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.gen_terms = GenSearchTermsPipeline()
        self.fetch_content = FetchUrlContentPipeline()

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
    ) -> Dict:
        """
        Research a single post: generate terms, search, fetch content.
        
        Args:
            post: Post dictionary
            step: Workflow step name
        
        Returns:
            Enriched post dictionary with search_results
        """
        post_id = post.get("id")
        if not post_id:
            raise ValueError("Post must have 'id' field")
        
        # Check if already researched (matching n8n "New" branch condition)
        if not self._is_new_post(post):
            return post
        
        # Generate search terms
        post_title = post.get("title")
        post_text = post.get("selftext") or post.get("text")
        post_url = post.get("url")
        
        try:
            search_terms = self.gen_terms.generate(
                post_id=post_id,
                post_title=post_title,
                post_text=post_text,
                post_url=post_url,
            )
        except Exception as e:
            print(f"Error generating search terms for {post_id}: {e}")
            search_terms = []
        
        if not search_terms:
            # No search terms generated, return post as-is
            return post
        
        # Search for each term and collect results
        all_search_results = []
        seen_links = set()
        
        for term in search_terms:
            try:
                search_response = self.backend.google_search(
                    query=term, first=1, count=10
                )
                results = search_response.get("results", [])
                
                for result in results:
                    link = result.get("link", "")
                    if not link:
                        continue
                    # Skip PDFs and duplicates
                    if link.endswith(".pdf"):
                        continue
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    all_search_results.append(result)
                    
            except Exception as e:
                print(f"Error searching for term '{term}': {e}")
                raise RuntimeError(
                    f"Google search failed for post {post_id} and term '{term}': {e}"
                ) from e
        
        # Fetch content for search results (in batches)
        fetched_texts = []
        batch_size = 3
        
        for i in range(0, len(all_search_results), batch_size):
            batch = all_search_results[i : i + batch_size]
            urls = [result.get("link") for result in batch if result.get("link")]
            if not urls:
                continue
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                futures = {
                    pool.submit(self.fetch_content.fetch, url, True): url
                    for url in urls
                }
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        fetch_result = future.result()
                        if fetch_result.success and fetch_result.text:
                            fetched_texts.append(fetch_result.text)
                    except Exception as e:
                        print(f"Error fetching URL {url}: {e}")
        
        # Update post with search results
        post["search_results"] = fetched_texts
        
        return post
    
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
            except Exception as e:
                print(f"Error loading post {file_name}: {e}")
        return self.process_post_objects(posts=posts, step=step)

    def process_post_objects(
        self,
        posts: List[Dict[str, Any]],
        step: str = "filter-researched",
    ) -> List[Dict[str, Any]]:
        """Process already-loaded post objects and persist researched versions."""
        researched_posts: List[Dict[str, Any]] = []
        for post in posts:
            post_id = post.get("id", "<unknown>")
            try:
                was_new = self._is_new_post(post)
                researched = self.research_post(post, step)
                self.backend.save_post_local(researched, step=step)
                if was_new:
                    try:
                        self.backend.save_post(researched, step=step)
                    except Exception as e:
                        print(f"Error saving post to backend for {post_id}: {e}")
                researched_posts.append(researched)
            except Exception as e:
                raise RuntimeError(f"Error processing post {post_id}: {e}") from e
        return researched_posts
