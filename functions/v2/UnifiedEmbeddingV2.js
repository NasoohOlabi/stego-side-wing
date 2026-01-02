/**
 * UnifiedEmbeddingV2.js
 *
 * Refined single n8n Code node that performs two-level steganographic
 * embedding with better validation, clearer constants, and safer bit handling.
 *
 * Improvements over UnifiedEmbedding.js:
 * - Correct parent lookup for comment chains (map by comment id, not name)
 * - Explicit bit-length handling with padding + warning flags
 * - Input validation for payload, comments, and angles
 * - Tunable constants extracted (literal cap, fuzzy search window/distance)
 * - Slightly faster dictionary matching (skip impossible matches early)
 * - Optional warnings surfaced in output for easier debugging
 */

/* ============================================================
   CONFIG
   ============================================================ */

const MAX_LITERAL_LEN = 250;
const MAX_FUZZY_CONTEXT = 400; // chars on each side when searching snippets
const MAX_FUZZY_DISTANCE = 12; // edit distance tolerance

/* ============================================================
   TYPE DEFINITIONS (JSDoc for n8n intellisense)
   ============================================================ */

/**
 * @typedef {Object} CommentContext
 * @property {string} name
 * @property {string} body
 * @property {string} id
 * @property {string} parent_id
 * @property {string} permalink
 */

/**
 * @typedef {Object} Angle
 * @property {string} source_quote
 * @property {string} tangent
 * @property {string} category
 * @property {number} [source_document]
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
 * @property {string} payload
 * @property {string} compressed
 * @property {number} compressedLength
 * @property {number} originalLength
 * @property {boolean} usedDict
 * @property {number} ratio
 * @property {Array<{doc: number|null, idx: number, len: number}>} references
 */

/**
 * @typedef {Object} CommentEmbeddingResult
 * @property {string} bitsUsed
 * @property {number} bitsCount
 * @property {number} flatCommentsLength
 * @property {number} selectionIndex
 * @property {"post"|"comment"} targetType
 * @property {PostContext} context
 * @property {CommentContext[]} pickedCommentChain
 * @property {boolean} [insufficientBits]
 */

/**
 * @typedef {Object} AngleEmbeddingResult
 * @property {string} bitsUsed
 * @property {number} bitsCount
 * @property {string} remainingBits
 * @property {Angle} selectedAngle
 * @property {Angle[]} remainingAngles
 * @property {Angle[]} totalAnglesSelectedFirst
 * @property {string|null} snippet
 * @property {number|null} selectedSourceDocumentIndex
 * @property {string|null} selectedSourceDocument
 * @property {boolean} [insufficientBits]
 */

/**
 * @typedef {Object} UnifiedEmbeddingOutput
 * @property {CompressionResult} compression
 * @property {CommentEmbeddingResult} commentEmbedding
 * @property {AngleEmbeddingResult} angleEmbedding
 * @property {number} totalBitsEmbedded
 * @property {string} fullEncodedBits
 * @property {string[]} warnings
 */

/* ============================================================
   HELPERS
   ============================================================ */

const isNonEmptyString = (v) => typeof v === "string" && v.length > 0;

/**
 * Safe bit slice with padding and warning flag.
 * @param {string} bits
 * @param {number} count
 */
function takeBits(bits, count) {
	if (count <= 0)
		return { bitsUsed: "", remaining: bits, insufficient: false };
	if (bits.length >= count) {
		return {
			bitsUsed: bits.substring(0, count),
			remaining: bits.substring(count),
			insufficient: false
		};
	}
	// Pad with zeros if insufficient; caller can surface warning.
	return {
		bitsUsed: bits.padEnd(count, "0"),
		remaining: "",
		insufficient: true
	};
}

/**
 * Levenshtein distance (used in fuzzy search).
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
 * Fuzzy search with bounded context.
 */
function fuzzySearchWithContext(
	longStr,
	shortStr,
	n = MAX_FUZZY_CONTEXT,
	maxDistance = MAX_FUZZY_DISTANCE
) {
	if (!isNonEmptyString(longStr) || !isNonEmptyString(shortStr)) return null;
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

function getBitWidth(max) {
	return max <= 1 ? 1 : Math.ceil(Math.log2(max + 1));
}

function encodeInt(n, max) {
	return n.toString(2).padStart(getBitWidth(max), "0");
}

function flattenComments(comments) {
	const flatten = (c) => {
		if (
			!c ||
			!c.replies ||
			!Array.isArray(c.replies) ||
			c.replies.length === 0
		) {
			return c ? [c] : [];
		}
		return [c, ...c.replies.flatMap(flatten)];
	};
	return Array.isArray(comments)
		? comments.flatMap(flatten).filter(Boolean)
		: [];
}

/* ============================================================
   COMPRESSION
   ============================================================ */

function buildDictionary(scrapeData) {
	if (!scrapeData || typeof scrapeData !== "object") return [];
	const searchResults = Array.isArray(scrapeData.search_results)
		? scrapeData.search_results
		: [];
	const comments = flattenComments(scrapeData.comments || []);
	const commentBodies = comments.map((c) => c.body).filter(isNonEmptyString);

	return [scrapeData.selftext, ...searchResults, ...commentBodies].filter(
		isNonEmptyString
	);
}

/**
 * Slightly optimized dictionary-based DP compression.
 */
function compressPayload(payload, dictionary) {
	const n = payload.length;
	const MAX_DICT_INDEX = dictionary.length;
	const MAX_MATCH_LEN = dictionary.reduce(
		(max, s) => Math.max(max, s.length || 0),
		0
	);

	// Precompute matches with a cheap first-character filter.
	const matches = new Map();
	for (let i = 0; i < n; i++) {
		const firstChar = payload[i];
		const matchesAtI = [];
		for (let j = 0; j < dictionary.length; j++) {
			const dictText = dictionary[j];
			if (!isNonEmptyString(dictText)) continue;
			let start = dictText.indexOf(firstChar);
			while (start !== -1) {
				let matchLen = 0;
				while (
					matchLen <
						Math.min(MAX_MATCH_LEN, n - i, dictText.length - start) &&
					payload[i + matchLen] === dictText[start + matchLen]
				) {
					matchLen++;
				}
				if (matchLen > 0)
					matchesAtI.push({ doc: j, idx: start, len: matchLen });
				start = dictText.indexOf(firstChar, start + 1);
			}
		}
		if (matchesAtI.length) matches.set(i, matchesAtI);
	}

	const dp = Array(n + 1).fill(+Infinity);
	const choice = Array(n).fill(null);
	dp[n] = 0;

	for (let i = n - 1; i >= 0; i--) {
		// Literal
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
		// Dictionary match
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
	const originalLength = toBinaryUtf8(payload).length;
	const finalCompressed = usedDict
		? "1" + compressed
		: "0" + toBinaryUtf8(payload);

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

function embedInCommentSelection(bits, post) {
	const comments = post?.comments || [];
	const flattenedComments = flattenComments(comments);
	const n = flattenedComments.length;

	const bitsCount = getBitWidth(n); // n+1 options (post + n comments)
	const { bitsUsed, remaining, insufficient } = takeBits(bits, bitsCount);

	let selectionIndex = parseInt(bitsUsed || "0", 2) || 0;
	if (selectionIndex > n) selectionIndex = selectionIndex % (n + 1);

	let pickedCommentChain = [];

	if (selectionIndex > 0 && n > 0) {
		const pickedComment = flattenedComments[selectionIndex - 1];
		const commentMap = new Map(flattenedComments.map((c) => [c.id, c])); // fixed: map by id

		const chain = [];
		let current = pickedComment;

		while (current) {
			chain.unshift(current);
			if (current.parent_id === current.link_id) break; // top-level comment
			current = commentMap.get(current.parent_id);
			if (current === pickedComment) break; // safety against loops
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
				id: post?.id,
				title: post?.title,
				author: post?.author,
				selftext: post?.selftext || "(No content)",
				subreddit: post?.subreddit,
				url: post?.url,
				permalink: post?.permalink
			},
			pickedCommentChain,
			insufficientBits: insufficient || undefined
		},
		bitsConsumed: bitsCount,
		remainingBits: remaining,
		insufficientBits: insufficient
	};
}

/* ============================================================
   EMBEDDING LEVEL 2: ANGLE SELECTION
   ============================================================ */

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
			snippet: null,
			insufficientBits: false
		};
	}

	const bitsCount = getBitWidth(angles.length - 1);
	const { bitsUsed, remaining, insufficient } = takeBits(bits, bitsCount);

	let idx = parseInt(bitsUsed || "0", 2) || 0;
	if (idx >= angles.length) idx = idx % angles.length;

	const selectedAngle = angles[idx];
	const remainingAngles = angles.filter((_, i) => i !== idx);

	const eq = (a, b) =>
		a.source_quote === b.source_quote &&
		a.tangent === b.tangent &&
		a.category === b.category;

	const totalAnglesSelectedFirst = [selectedAngle, ...remainingAngles].map(
		(angle) => ({
			...angle,
			source_document: nestedAngles.findIndex((docAngles) =>
				docAngles.some((o) => eq(o, angle))
			)
		})
	);

	const sourceDocIdx = totalAnglesSelectedFirst[0].source_document;
	const sourceDoc =
		sourceDocIdx >= 0 && sourceDocIdx < documents.length
			? documents[sourceDocIdx]
			: null;

	const snippet = sourceDoc
		? fuzzySearchWithContext(
				sourceDoc,
				selectedAngle.source_quote,
				MAX_FUZZY_CONTEXT,
				MAX_FUZZY_DISTANCE
		  )
		: null;

	return {
		bitsUsed,
		bitsCount,
		remainingBits: remaining,
		selectedAngle,
		remainingAngles,
		totalAnglesSelectedFirst,
		snippet,
		selectedSourceDocumentIndex: sourceDocIdx >= 0 ? sourceDocIdx : null,
		selectedSourceDocument: sourceDoc ?? null,
		insufficientBits: insufficient || undefined
	};
}

/* ============================================================
   MAIN
   ============================================================ */

const results = $input.all().map((item) => {
	const warnings = [];

	// Step 1: unwrap data
	let data = item.json;
	if (data?.angles && data?.data) {
		data = { ...data.data, angles: data.angles };
	}

	const post = data?.post || data || {};

	// Step 2: payload extraction + validation
	let payload = $("SetSecretData").first()?.json?.payload;
	if (payload && typeof payload === "object" && payload.payload) {
		payload = payload.payload;
	}
	if (!isNonEmptyString(payload)) {
		warnings.push("No payload found or payload is not a non-empty string.");
		return { json: { error: "No payload found", input: post, warnings } };
	}

	// Step 3: angles normalization
	const nestedAngles = (post?.angles || data?.angles || [])
		.filter(Boolean)
		.map((x) => (Array.isArray(x) ? x.filter(Boolean) : [x]));

	// Step 4: build dictionary & compress
	const dictionary = buildDictionary(post);
	const compression = compressPayload(payload, dictionary);

	// Step 5: level 1 embedding (comments)
	const {
		result: commentEmbedding,
		bitsConsumed: commentBits,
		remainingBits: afterComment,
		insufficientBits: commentShort
	} = embedInCommentSelection(compression.compressed, post);
	if (commentShort)
		warnings.push(
			"Insufficient bits for comment selection; padded with zeros."
		);

	// Step 6: level 2 embedding (angles)
	const documents = Array.isArray(post?.search_results)
		? post.search_results
		: [];
	const angleEmbedding = embedInAngleSelection(
		afterComment,
		nestedAngles,
		documents
	);
	if (angleEmbedding.insufficientBits)
		warnings.push(
			"Insufficient bits for angle selection; padded with zeros."
		);

	// Step 7: output
	const totalBitsEmbedded =
		commentEmbedding.bitsCount + angleEmbedding.bitsCount;
	const fullEncodedBits = commentEmbedding.bitsUsed + angleEmbedding.bitsUsed;

	/** @type {UnifiedEmbeddingOutput} */
	const output = {
		compression,
		commentEmbedding,
		angleEmbedding,
		totalBitsEmbedded,
		fullEncodedBits,
		warnings
	};

	return { json: output };
});

return results;
