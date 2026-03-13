import json
import os
from typing import Any, Dict, List

# Define input and output paths
POSTS_FILE_PATH = "datasets/2024/r_news_posts.jsonl"
COMMENTS_FILE_PATH = "datasets/2024/r_news_comments.jsonl"
OUTPUT_DIR = "datasets/2024/news"


def load_posts(filepath: str) -> Dict[str, Any]:
    """
    Loads posts from the JSONL file into a dictionary, keyed by 't3_ID' for
    easy matching with comment link_id.
    """
    print(f"Loading posts from {filepath}...")
    posts = {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    post = json.loads(line)
                    # Use the 'name' field (e.g., 't3_ltlz1') as the key for linking
                    posts[post["name"]] = post
        print(f"Loaded {len(posts)} posts.")
        return posts
    except FileNotFoundError:
        print(f"Error: Post file not found at {filepath}")
        return {}
    except Exception as e:
        print(f"An error occurred while loading posts: {e}")
        return {}


def group_comments_by_parent(filepath: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Loads comments and groups them into a dictionary where the key is the
    parent_id (either t3_ID for a post or t1_ID for another comment) and
    the value is a list of child comments.
    """
    print(f"Loading and grouping comments from {filepath}...")
    comments_by_parent = {}
    comment_count = 0

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    comment = json.loads(line)
                    parent_id = comment.get("parent_id")

                    if parent_id:
                        if parent_id not in comments_by_parent:
                            comments_by_parent[parent_id] = []

                        # Initialize 'replies' list for future nesting
                        comment["replies"] = []
                        comments_by_parent[parent_id].append(comment)
                        comment_count += 1

        print(f"Loaded and grouped {comment_count} comments.")
        return comments_by_parent
    except FileNotFoundError:
        print(f"Error: Comment file not found at {filepath}")
        return {}
    except Exception as e:
        print(f"An error occurred while loading comments: {e}")
        return {}


def nest_comments(parent_id: str, comments_by_parent: Dict[str, List[Dict[str, Any]]]):
    """
    Recursively finds and attaches replies to a given parent ID.
    The parent_id can be a post ID (t3_) or a comment ID (t1_).
    """
    # Check if the parent has children (replies)
    children = comments_by_parent.get(parent_id, [])

    # Sort children by score to maintain a sensible order (optional)
    children.sort(key=lambda c: c.get("score", 0), reverse=True)

    for child in children:
        # The ID for a comment when it is a parent is its 'name' (e.g., 't1_c2vi30n')
        child_parent_id = child.get("name")

        # Recursively find and attach replies to this child comment
        # The result of the recursive call is the list of replies, which is stored
        child["replies"] = nest_comments(str(child_parent_id), comments_by_parent)

    return children


def main():
    """
    Main function to orchestrate the loading, nesting, and export process.
    """
    # 1. Ensure the output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 2. Load all posts
    posts_data = load_posts(POSTS_FILE_PATH)
    if not posts_data:
        print("Aborting due to no posts loaded.")
        return

    # 3. Group all comments by their parent
    comments_by_parent = group_comments_by_parent(COMMENTS_FILE_PATH)
    if not comments_by_parent:
        print("Warning: No comments loaded. Posts will be exported without comments.")

    posts_exported = 0

    # 4. Iterate through posts, nest comments, and export
    for post_id_t3, post in posts_data.items():
        # The post_id_t3 is the 't3_ID' (e.g., 't3_ltlz1')

        # Find all top-level comments for this post and recursively nest their replies
        top_level_comments = nest_comments(post_id_t3, comments_by_parent)

        # Add the nested comments list to the post object
        post["comments"] = top_level_comments

        # Prepare for export
        # The output filename uses the raw ID (e.g., 'ltlz1')
        raw_post_id = post.get("id")
        if raw_post_id:
            output_filepath = os.path.join(OUTPUT_DIR, f"{raw_post_id}.json")

            try:
                with open(output_filepath, "w", encoding="utf-8") as outfile:
                    json.dump(post, outfile, indent=4)
                posts_exported += 1
            except Exception as e:
                print(f"Error exporting post {raw_post_id}: {e}")
        else:
            print(f"Skipping post with missing raw ID: {post_id_t3}")

    print("\n--- Processing Complete ---")
    print(f"Total posts exported: {posts_exported}")
    print(f"Output files stored in: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
