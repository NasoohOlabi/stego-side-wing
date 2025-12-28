/**
 * @typedef {Object} CommentContext
 * @property {string} name
 * @property {string} body
 * @property {string} id
 * @property {string} parent_id
 * @property {string} permalink
 */

// Loop through every item received from the previous node
const results = $input.all().map((item) => {
	/** @type {any} */
	const post = item.json.post || item.json;
	/** @type {any[]} */
	const comments = post.comments || [];

	/**
	 * Recursive function to flatten Reddit-style comment trees
	 * @param {any} c
	 * @returns {any[]}
	 */
	const flatten = (c) => {
		if (!c.replies || !Array.isArray(c.replies) || c.replies.length === 0) {
			return [c];
		}
		return [c, ...c.replies.flatMap(flatten)];
	};

	const flattenedComments = comments.flatMap(flatten);
	const n = flattenedComments.length;

	// Probability Logic: 1/(n+1) chance for each comment OR the post itself
	const randomIndex = Math.floor(Math.random() * (n + 1));

	/** @type {CommentContext[]} */
	let contextCommentChain = [];

	// If randomIndex > 0, we process the specific comment path
	if (randomIndex > 0 && n > 0) {
		const pickedComment = flattenedComments[randomIndex - 1];
		const commentMap = new Map(flattenedComments.map((c) => [c.name, c]));

		/** @type {any[]} */
		const chain = [];
		let current = pickedComment;

		while (current) {
			chain.unshift(current);
			if (current.parent_id === current.link_id) break;
			current = commentMap.get(current.parent_id);
		}

		// Map the raw data into our typed CommentContext structure
		contextCommentChain = chain.map((c) => ({
			name: c.author || c.author_fullname || "Unknown",
			body: c.body || "",
			id: c.id,
			parent_id: c.parent_id,
			permalink: c.permalink
		}));
	}

	return {
		json: {
			flatCommentsLength: n,
			selectionIndex: randomIndex, // 0 = Post, 1+ = Comment
			targetType: randomIndex === 0 ? "post" : "comment",
			context: {
				id: post.id,
				title: post.title,
				author: post.author,
				selftext: post.selftext || "(No content)",
				subreddit: post.subreddit,
				url: post.url,
				permalink: post.permalink,
				picked_comment_context_chain: contextCommentChain
			}
		}
	};
});

return results;
