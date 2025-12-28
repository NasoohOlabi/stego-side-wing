/**
 * UnifiedEmbedding.js
 *
 * A single n8n Code node that replaces:
 * - WrapPost.js
 * - Pick comment.js
 * - TextBasedCompression.js
 * - AngleSelection.js
 *
 * Performs two-level steganographic embedding:
 * 1. Level 1: Encode bits into comment selection
 * 2. Level 2: Encode remaining bits into angle selection
 */

/* ============================================================
   TYPE DEFINITIONS
   ============================================================ */

/**
 * @typedef {Object} CommentContext
 * @property {string} name - Author name
 * @property {string} body - Comment body text
 * @property {string} id - Comment ID
 * @property {string} parent_id - Parent comment/post ID
 * @property {string} permalink - Permalink to comment
 */

/**
 * @typedef {Object} Angle
 * @property {string} source_quote - Quote from source
 * @property {string} tangent - The tangent/angle text
 * @property {string} category - Category of the angle
 * @property {number} [source_document] - Index of source document
 */

/**
 * @typedef {Object} PostContext
 * @property {string} id
 * @property {string} title
 * @property {string} author
 * @property {string} selftext
 * @property {string} subreddit
 * @property {string} url
 * @property {string} permalink
 */

/**
 * @typedef {Object} CompressionResult
 * @property {string} payload - Original payload
 * @property {string} compressed - Compressed binary string
 * @property {number} compressedLength - Length of compressed string
 * @property {number} originalLength - Length of original UTF-8 binary
 * @property {boolean} usedDict - Whether dictionary was used
 * @property {number} ratio - Compression ratio
 * @property {Array<{doc: number|null, idx: number, len: number}>} references - Compression references
 */

/**
 * @typedef {Object} CommentEmbeddingResult
 * @property {string} bitsUsed - Binary string of bits used
 * @property {number} bitsCount - Number of bits used
 * @property {number} flatCommentsLength - Total flattened comments count
 * @property {number} selectionIndex - Selected index (0=post, 1+=comment)
 * @property {"post"|"comment"} targetType - Type of selection
 * @property {PostContext} context - Post context
 * @property {CommentContext[]} pickedCommentChain - Comment chain to selected
 */

/**
 * @typedef {Object} AngleEmbeddingResult
 * @property {string} bitsUsed - Binary string of bits used
 * @property {number} bitsCount - Number of bits used
 * @property {string} remainingBits - Remaining payload bits
 * @property {Angle} selectedAngle - The selected angle
 * @property {Angle[]} remainingAngles - Other angles
 * @property {Angle[]} totalAnglesSelectedFirst - All angles with selected first
 * @property {string|null} snippet - Fuzzy matched snippet with context
 */

/**
 * @typedef {Object} UnifiedEmbeddingOutput
 * @property {CompressionResult} compression - Compression results
 * @property {CommentEmbeddingResult} commentEmbedding - Level 1 embedding
 * @property {AngleEmbeddingResult} angleEmbedding - Level 2 embedding
 * @property {number} totalBitsEmbedded - Total bits used for embedding
 * @property {string} fullEncodedBits - All bits used for both levels
 */

/* ============================================================
   HELPER FUNCTIONS
   ============================================================ */

/**
 * Calculates the Levenshtein distance between two strings.
 * @param {string} str1 - First string
 * @param {string} str2 - Second string
 * @returns {number} Edit distance >= 0
 */
function levenshteinDistance(str1, str2) {
	const matrix = [];
	for (let i = 0; i <= str2.length; i++) matrix[i] = [i];
	for (let j = 0; j <= str1.length; j++) matrix[0][j] = j;

	for (let i = 1; i <= str2.length; i++) {
		for (let j = 1; j <= str1.length; j++) {
			if (str2[i - 1] === str1[j - 1]) {
				matrix[i][j] = matrix[i - 1][j - 1];
			} else {
				matrix[i][j] = Math.min(
					matrix[i - 1][j - 1] + 1,
					matrix[i][j - 1] + 1,
					matrix[i - 1][j] + 1
				);
			}
		}
	}
	return matrix[str2.length][str1.length];
}

/**
 * Performs fuzzy search for shortStr in longStr with context.
 * @param {string} longStr - Text to search in
 * @param {string} shortStr - Pattern to find
 * @param {number} n - Context characters on each side
 * @param {number} [maxDistance=2] - Max edit distance
 * @returns {string|null} Match with context or null
 */
function fuzzySearchWithContext(longStr, shortStr, n, maxDistance = 2) {
	if (!longStr || !shortStr || shortStr.length === 0) return null;
	if (typeof n !== "number" || n < 0) n = 0;

	const lowerLong = longStr.toLowerCase();
	const lowerShort = shortStr.toLowerCase();
	const shortLen = shortStr.length;

	const tolerance = Math.min(maxDistance, Math.floor(shortLen / 2));
	const minWindow = Math.max(1, shortLen - tolerance);
	const maxWindow = shortLen + tolerance;

	for (let i = 0; i < lowerLong.length; i++) {
		for (let len = minWindow; len <= maxWindow; len++) {
			if (i + len > lowerLong.length) break;
			const candidate = lowerLong.substring(i, i + len);
			const distance = levenshteinDistance(candidate, lowerShort);
			if (distance <= tolerance) {
				const start = Math.max(0, i - n);
				const end = Math.min(longStr.length, i + len + n);
				return longStr.substring(start, end);
			}
		}
	}
	return null;
}

/**
 * Converts a string to binary UTF-8 representation.
 * @param {string} str - Input string
 * @returns {string} Binary string
 */
function toBinaryUtf8(str) {
	const bytes = [];
	for (let i = 0; i < str.length; i++) {
		let cp = str.codePointAt(i);
		if (cp > 0xffff) i++;
		if (cp <= 0x7f) bytes.push(cp);
		else if (cp <= 0x7ff) bytes.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
		else if (cp <= 0xffff)
			bytes.push(
				0xe0 | (cp >> 12),
				0x80 | ((cp >> 6) & 0x3f),
				0x80 | (cp & 0x3f)
			);
		else
			bytes.push(
				0xf0 | (cp >> 18),
				0x80 | ((cp >> 12) & 0x3f),
				0x80 | ((cp >> 6) & 0x3f),
				0x80 | (cp & 0x3f)
			);
	}
	return bytes.map((b) => b.toString(2).padStart(8, "0")).join("");
}

/**
 * Gets bit width needed to represent a max value.
 * @param {number} max - Maximum value
 * @returns {number} Number of bits needed
 */
function getBitWidth(max) {
	return max <= 1 ? 1 : Math.ceil(Math.log2(max + 1));
}

/**
 * Encodes an integer to binary with specified width.
 * @param {number} n - Number to encode
 * @param {number} max - Maximum possible value
 * @returns {string} Binary string
 */
function encodeInt(n, max) {
	return n.toString(2).padStart(getBitWidth(max), "0");
}

/**
 * Recursively flattens a Reddit-style comment tree.
 * @param {any[]} comments - Array of comments with replies
 * @returns {any[]} Flattened array
 */
function flattenComments(comments) {
	/** @param {any} c */
	const flatten = (c) => {
		if (!c.replies || !Array.isArray(c.replies) || c.replies.length === 0) {
			return [c];
		}
		return [c, ...c.replies.flatMap(flatten)];
	};
	return (comments || []).flatMap(flatten);
}

/* ============================================================
   COMPRESSION LOGIC
   ============================================================ */

/**
 * Builds dictionary from post data.
 * @param {any} scrapeData - Post data with selftext, search_results, comments
 * @returns {string[]} Dictionary of strings
 */
function buildDictionary(scrapeData) {
	const searchResults = scrapeData.search_results || [];
	const comments = flattenComments(scrapeData.comments || []);
	const commentBodies = comments.map((c) => c.body).filter(Boolean);

	return [scrapeData.selftext, ...searchResults, ...commentBodies].filter(
		(x) => typeof x === "string" && x.length > 0
	);
}

/**
 * Compresses payload using dictionary-based DP compression.
 * @param {string} payload - Text to compress
 * @param {string[]} dictionary - Dictionary for compression
 * @returns {CompressionResult}
 */
function compressPayload(payload, dictionary) {
	const n = payload.length;
	const MAX_LITERAL_LEN = 250;
	const MAX_DICT_INDEX = dictionary.length;
	const MAX_MATCH_LEN = dictionary.reduce(
		(max, s) => Math.max(max, s.length),
		0
	);

	// Find all matches
	const matches = new Map();
	for (let i = 0; i < n; i++) {
		const matchesAtI = [];
		for (let j = 0; j < dictionary.length; j++) {
			const dictText = dictionary[j];
			for (let k = 0; k < dictText.length; k++) {
				let matchLen = 0;
				while (
					matchLen < Math.min(MAX_MATCH_LEN, n - i, dictText.length - k) &&
					payload[i + matchLen] === dictText[k + matchLen]
				) {
					matchLen++;
				}
				if (matchLen > 0)
					matchesAtI.push({ doc: j, idx: k, len: matchLen });
			}
		}
		if (matchesAtI.length) matches.set(i, matchesAtI);
	}

	// DP for optimal compression
	const dp = Array(n + 1).fill(+Infinity);
	const choice = Array(n).fill(null);
	dp[n] = 0;

	for (let i = n - 1; i >= 0; i--) {
		// Literal option
		for (let L = 1; L <= Math.min(MAX_LITERAL_LEN, n - i); L++) {
			const cost =
				1 +
				getBitWidth(MAX_LITERAL_LEN) +
				toBinaryUtf8(payload.slice(i, i + L)).length +
				dp[i + L];
			if (cost < dp[i]) {
				choice[i] = { kind: "literal", len: L };
				dp[i] = cost;
			}
		}

		// Dictionary match option
		const matchesAtI = matches.get(i) || [];
		for (const match of matchesAtI) {
			const cost =
				1 +
				getBitWidth(MAX_DICT_INDEX) +
				getBitWidth(dictionary[match.doc].length) +
				getBitWidth(MAX_MATCH_LEN) +
				dp[i + match.len];
			if (cost < dp[i]) {
				choice[i] = { kind: "dict", ...match };
				dp[i] = cost;
			}
		}
	}

	// Build compressed string
	let currI = 0;
	let compressed = "";
	/** @type {Array<{doc: number|null, idx: number, len: number}>} */
	const references = [];

	while (currI < n) {
		const ch = choice[currI] || { kind: "literal", len: 1 };
		if (ch.kind === "literal") {
			compressed +=
				"0" +
				encodeInt(ch.len, MAX_LITERAL_LEN) +
				toBinaryUtf8(payload.slice(currI, currI + ch.len));
			references.push({ doc: null, idx: currI, len: ch.len });
		} else {
			compressed +=
				"1" +
				encodeInt(ch.doc, MAX_DICT_INDEX) +
				encodeInt(ch.idx, dictionary[ch.doc].length) +
				encodeInt(ch.len, MAX_MATCH_LEN);
			references.push({ doc: ch.doc, idx: ch.idx, len: ch.len });
		}
		currI += ch.len;
	}

	const usedDict = references.some((r) => r.doc !== null);
	const finalCompressed = usedDict
		? "1" + compressed
		: "0" + toBinaryUtf8(payload);
	const originalLength = toBinaryUtf8(payload).length;

	return {
		payload,
		compressed: finalCompressed,
		compressedLength: finalCompressed.length,
		originalLength,
		usedDict,
		ratio: finalCompressed.length / (originalLength || 1),
		references
	};
}

/* ============================================================
   EMBEDDING LEVEL 1: COMMENT SELECTION
   ============================================================ */

/**
 * Embeds bits into comment selection.
 * @param {string} bits - Binary string to read from
 * @param {any} post - Post data with comments
 * @returns {{result: CommentEmbeddingResult, bitsConsumed: number}}
 */
function embedInCommentSelection(bits, post) {
	const comments = post.comments || [];
	const flattenedComments = flattenComments(comments);
	const n = flattenedComments.length;

	// Fixed-width bits needed: ceil(log2(n+1)) for 0..n range
	const bitsCount = getBitWidth(n); // n+1 options (post + n comments)
	const bitsUsed = bits.substring(0, bitsCount);

	// Decode selection index
	let selectionIndex = parseInt(bitsUsed, 2) || 0;
	// Clamp to valid range
	if (selectionIndex > n) selectionIndex = selectionIndex % (n + 1);

	// Build comment chain if comment selected
	/** @type {CommentContext[]} */
	let pickedCommentChain = [];

	if (selectionIndex > 0 && n > 0) {
		const pickedComment = flattenedComments[selectionIndex - 1];
		const commentMap = new Map(flattenedComments.map((c) => [c.name, c]));

		/** @type {any[]} */
		const chain = [];
		let current = pickedComment;

		while (current) {
			chain.unshift(current);
			if (current.parent_id === current.link_id) break;
			current = commentMap.get(current.parent_id);
		}

		pickedCommentChain = chain.map((c) => ({
			name: c.author || c.author_fullname || "Unknown",
			body: c.body || "",
			id: c.id,
			parent_id: c.parent_id,
			permalink: c.permalink
		}));
	}

	return {
		result: {
			bitsUsed,
			bitsCount,
			flatCommentsLength: n,
			selectionIndex,
			targetType: selectionIndex === 0 ? "post" : "comment",
			context: {
				id: post.id,
				title: post.title,
				author: post.author,
				selftext: post.selftext || "(No content)",
				subreddit: post.subreddit,
				url: post.url,
				permalink: post.permalink
			},
			pickedCommentChain
		},
		bitsConsumed: bitsCount
	};
}

/* ============================================================
   EMBEDDING LEVEL 2: ANGLE SELECTION
   ============================================================ */

/**
 * Embeds bits into angle selection.
 * @param {string} bits - Binary string to read from
 * @param {Angle[][]} nestedAngles - 2D array of angles by document
 * @param {string[]} documents - Document texts for snippet search
 * @returns {AngleEmbeddingResult}
 */
function embedInAngleSelection(bits, nestedAngles, documents) {
	const angles = nestedAngles.flat().filter(Boolean);

	if (angles.length === 0) {
		return {
			bitsUsed: "",
			bitsCount: 0,
			remainingBits: bits,
			selectedAngle: /** @type {Angle} */ ({}),
			remainingAngles: [],
			totalAnglesSelectedFirst: [],
			snippet: null
		};
	}

	// Fixed-width bits for angle selection
	const bitsCount = getBitWidth(angles.length - 1);
	const bitsUsed = bits.substring(0, bitsCount);
	const remainingBits = bits.substring(bitsCount);

	// Decode angle index
	let idx = parseInt(bitsUsed, 2) || 0;
	if (idx >= angles.length) idx = idx % angles.length;

	const selectedAngle = angles[idx];
	const remainingAngles = angles.filter((_, i) => i !== idx);

	// Deep equality for angle objects
	const eq = (a, b) =>
		a.source_quote === b.source_quote &&
		a.tangent === b.tangent &&
		a.category === b.category;

	// Tag angles with source document index
	const totalAnglesSelectedFirst = [selectedAngle, ...remainingAngles].map(
		(angle) => ({
			...angle,
			source_document: nestedAngles.findIndex((docAngles) =>
				docAngles.some((o) => eq(o, angle))
			)
		})
	);

	// Find snippet from source document
	const sourceDocIdx = totalAnglesSelectedFirst[0].source_document;
	const sourceDoc =
		sourceDocIdx >= 0 && sourceDocIdx < documents.length
			? documents[sourceDocIdx]
			: null;

	const snippet = sourceDoc
		? fuzzySearchWithContext(sourceDoc, selectedAngle.source_quote, 1000, 20)
		: null;

	return {
		bitsUsed,
		bitsCount,
		remainingBits,
		selectedAngle,
		remainingAngles,
		totalAnglesSelectedFirst,
		snippet
	};
}

/* ============================================================
   MAIN ENTRY POINT
   ============================================================ */

/**
 * Process all input items with unified embedding.
 *
 * Expected input structure:
 * - item.json.post OR item.json: Post data with comments, angles, search_results
 * - item.json.payload OR item.json.payload.payload: Secret payload string
 * - item.json.angles: Nested angles array (2D)
 */
const results = $input.all().map((item) => {
	// ========== STEP 1: Unwrap Post (merge data) ==========
	/** @type {any} */
	let data = item.json;
	if (data.angles && data.data) {
		data = { ...data.data, angles: data.angles };
	}

	/** @type {any} */
	const post = data.post || data;

	// Extract payload (handle nested structure)
	let payload = $("SetSecretData").first().json.payload;
	if (typeof payload === "object" && payload.payload) {
		payload = payload.payload;
	}
	if (!payload || typeof payload !== "string") {
		return { json: { error: "No payload found", input: post } };
	}

	// Extract nested angles
	/** @type {Angle[][]} */
	const nestedAngles = (post.angles || data.angles || [])
		.filter(Boolean)
		.map((x) => (Array.isArray(x) ? x.filter(Boolean) : [x]));

	// ========== STEP 2: BUILD DICTIONARY & COMPRESS ==========
	const dictionary = buildDictionary(post);
	const compression = compressPayload(payload, dictionary);

	// ========== STEP 3: LEVEL 1 - COMMENT EMBEDDING ==========
	const { result: commentEmbedding, bitsConsumed: commentBits } =
		embedInCommentSelection(compression.compressed, post);

	// ========== STEP 4: LEVEL 2 - ANGLE EMBEDDING ==========
	const bitsAfterComment = compression.compressed.substring(commentBits);

	// Use search_results as documents for snippet lookup (replaces KeywordsSetsGeneration)
	const documents = post.search_results || [];
	const angleEmbedding = embedInAngleSelection(
		bitsAfterComment,
		nestedAngles,
		documents
	);

	// ========== STEP 5: BUILD OUTPUT ==========
	const totalBitsEmbedded =
		commentEmbedding.bitsCount + angleEmbedding.bitsCount;
	const fullEncodedBits = commentEmbedding.bitsUsed + angleEmbedding.bitsUsed;

	/** @type {UnifiedEmbeddingOutput} */
	const output = {
		compression,
		commentEmbedding,
		angleEmbedding,
		totalBitsEmbedded,
		fullEncodedBits
	};

	return { json: output };
});

return results;
