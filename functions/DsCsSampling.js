// The input is expected to be an array of items, we'll work with the first item's JSON
const post = $input.first().json;
const comments = post.result.comments || []; // Ensure comments is an array

// 1. Define the flattening function (cf), as you already had it
// It recursively flattens the comment tree into a single array
const cf = (c) =>
	c.replies.length === 0 ? [c] : [c, ...c.replies.flatMap((x) => cf(x))];

// 2. Flatten all comments from the post
const flattenedComments = comments.flatMap((x) => cf(x));
const flatLength = flattenedComments.length;

// 3. Random Draw Logic: 50% chance to select a comment, 50% chance to select nothing
// The selection pool size is flatLength + 1 (the +1 represents the "no selection" option)
const selectionPoolSize = flatLength + 1;
const randomChoice = Math.floor(Math.random() * selectionPoolSize); // 0 to flatLength

// Initialize return variables
let pickedComment = null;
let randomIndex = -1; // -1 indicates 'no comment picked'
let contextCommentChain = null;

// Check if a comment was picked (index 0 to flatLength - 1)
if (randomChoice < flatLength) {
	// A comment was picked
	randomIndex = randomChoice;
	pickedComment = flattenedComments[randomIndex];

	// 4. Trace the path (context chain) from the post down to the picked comment
	// This function finds the parent of a given comment within the top-level comments or any of their replies.
	const findContextChain = (targetComment) => {
		const chain = [];
		let currentComment = targetComment;

		// The logic below assumes each comment object in the tree *retains* the 'parent_id' field
		// which is standard for Reddit comment JSON.

		// If the target is a top-level comment, its parent_id will match the post's id (link_id)
		const isTopLevel = (c) => c.parent_id === c.link_id;

		// Walk up the tree using parent_id until the link_id is reached
		while (currentComment && !isTopLevel(currentComment)) {
			chain.unshift(currentComment); // Add the child to the front of the chain

			// Find the parent object by its ID in the flattened list (or in the post's top comments)
			// A simple map for O(1) lookup would be faster for very large lists,
			// but a linear search here is simple enough and avoids preprocessing.

			// We search the flattened list for the parent
			const parent = flattenedComments.find(
				(c) => c.name === currentComment.parent_id
			);

			if (parent) {
				currentComment = parent;
			} else {
				// Must be the top-level parent (which is in the 'comments' array)
				currentComment = comments.find(
					(c) => c.name === currentComment.parent_id
				);
			}
		}

		// Add the final top-level comment to the front of the chain
		if (currentComment) {
			chain.unshift(currentComment);
		}

		return chain;
	};

	const fullChain = findContextChain(pickedComment);

	// 5. Build the nested context object (A -> B -> C -> D)
	// We'll use the 'project_comment' logic to clean up the objects for the final output
	const project_comment = (c) =>
		!c
			? {}
			: {
					author_fullname: c.author_fullname || "",
					body: c.body,
					id: c.id,
					name: c.name,
					parent_id: c.parent_id,
					score: c.score,
					created_utc: c.created_utc,
					permalink: c.permalink
					// Only keep necessary fields, less clutter
			  };

	let currentNestedContext = null;

	// Iterate the full chain backwards to build the nested structure
	for (let i = fullChain.length - 1; i >= 0; i--) {
		const commentData = project_comment(fullChain[i]);

		if (currentNestedContext === null) {
			// This is the last comment (the picked one)
			currentNestedContext = { ...commentData, replies: [] };
		} else {
			// This is an ancestor; wrap the current nested context in its 'replies'
			currentNestedContext = {
				...commentData,
				replies: [currentNestedContext]
			};
		}
	}
	contextCommentChain = currentNestedContext;
} else {
	// No comment was picked (randomChoice == flatLength)
	// randomIndex remains -1 and contextCommentChain remains null
}

// 6. Final Return Structure

return [
	{
		// 1. Flattened info
		flatCommentsLength: flatLength,
		randomPickedIndex: randomIndex, // -1 if no comment was picked
		post,
		// 2. Context
		context: {
			id: post.id,
			title: post.title,
			author: post.author,
			subreddit: post.subreddit,
			created_utc: post.created_utc,
			score: post.score,
			num_comments: post.num_comments,
			url: post.url,
			selftext: post.selftext,
			permalink: post.permalink,

			// This will be the nested path (A->B->C->D) or null if no comment was picked
			picked_comment_context_chain: contextCommentChain
		}
	}
];
