/**
 * UnifiedEmbeddingV3_Optimized.js
 *
 * High-performance single n8n Code node that performs two-level steganographic
 * embedding.
 *
 * OPTIMIZATIONS:
 * - Pre-calculated byte-width lookups (removes repeated UTF-8 conversions in DP loop).
 * - Exact match shortcut for angle search (skips Levenshtein if not needed).
 * - Memory-efficient Levenshtein implementation (2-row buffer).
 * - Fallback to standard encoding if dictionary compression is inefficient.
 */

/* ============================================================
   CONFIG
   ============================================================ */

const MAX_LITERAL_LEN = 250;
const MAX_FUZZY_CONTEXT = 400; // chars on each side
const MAX_FUZZY_DISTANCE = 12; // edit distance tolerance

/* ============================================================
   HELPERS
   ============================================================ */

const isNonEmptyString = (v) => typeof v === "string" && v.length > 0;

/**
 * Safe bit slice with padding.
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
	return {
		bitsUsed: bits.padEnd(count, "0"),
		remaining: "",
		insufficient: true
	};
}

/**
 * Memory-optimized Levenshtein distance (Two-Row algorithm).
 * Reduces memory overhead significantly compared to full matrix.
 */
function levenshteinDistance(s, t) {
	if (s === t) return 0;
	if (s.length === 0) return t.length;
	if (t.length === 0) return s.length;

	// Optimization: Ensure t is the smaller string for array allocation
	if (s.length < t.length) [s, t] = [t, s];

	let v0 = new Int32Array(t.length + 1);
	let v1 = new Int32Array(t.length + 1);

	for (let i = 0; i <= t.length; i++) v0[i] = i;

	for (let i = 0; i < s.length; i++) {
		v1[0] = i + 1;
		for (let j = 0; j < t.length; j++) {
			const cost = s[i] === t[j] ? 0 : 1;
			v1[j + 1] = Math.min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost);
		}
		// Swap arrays for next iteration
		const temp = v0;
		v0 = v1;
		v1 = temp;
	}
	return v0[t.length];
}

/**
 * Optimized Fuzzy search.
 * 1. Tries simple `includes` (fast).
 * 2. Falls back to sliding window Levenshtein (slow) only if needed.
 */
function fuzzySearchWithContext(
	longStr,
	shortStr,
	n = MAX_FUZZY_CONTEXT,
	maxDistance = MAX_FUZZY_DISTANCE
) {
	if (!isNonEmptyString(longStr) || !isNonEmptyString(shortStr)) return null;

	// 1. FAST PATH: Exact match
	const exactIndex = longStr.indexOf(shortStr);
	if (exactIndex !== -1) {
		const start = Math.max(0, exactIndex - n);
		const end = Math.min(longStr.length, exactIndex + shortStr.length + n);
		return longStr.substring(start, end);
	}

	// 2. SLOW PATH: Fuzzy match
	const lowerLong = longStr.toLowerCase();
	const lowerShort = shortStr.toLowerCase();
	const shortLen = shortStr.length;
	const tolerance = Math.min(maxDistance, Math.floor(shortLen / 2));
	const minWindow = Math.max(1, shortLen - tolerance);
	const maxWindow = shortLen + tolerance;

	for (let i = 0; i < lowerLong.length; i++) {
		// Optimization: Skip loop if remaining string is too short
		if (lowerLong.length - i < minWindow) break;

		for (let len = minWindow; len <= maxWindow; len++) {
			if (i + len > lowerLong.length) break;
			const candidate = lowerLong.substring(i, i + len);

			// Optimization: Quick check first/last chars before doing full Levenshtein
			// (Heuristic: usually fuzzy matches share at least some boundaries or length proximity)
			if (Math.abs(candidate.length - lowerShort.length) > tolerance)
				continue;

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
 * Converts string to binary representation of its UTF-8 bytes.
 */
function toBinaryUtf8(str) {
	const output = [];
	for (let i = 0; i < str.length; i++) {
		let cp = str.codePointAt(i);
		if (cp > 0xffff) i++;

		let bytes = [];
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

		for (const b of bytes) output.push(b.toString(2).padStart(8, "0"));
	}
	return output.join("");
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
	return [
		scrapeData.selftext,
		...searchResults,
		...comments.map((c) => c.body)
	].filter(isNonEmptyString);
}

/**
 * Calculates the UTF-8 byte length of a string directly without binary conversion.
 * Used for fast cost estimation in DP.
 */
function getUtf8ByteLength(str) {
	let len = 0;
	for (let i = 0; i < str.length; i++) {
		let code = str.codePointAt(i);
		if (code > 0xffff) i++; // Surrogate pair
		if (code <= 0x7f) len += 1;
		else if (code <= 0x7ff) len += 2;
		else if (code <= 0xffff) len += 3;
		else len += 4;
	}
	return len;
}

/**
 * Optimized dictionary compression.
 */
function compressPayload(payload, dictionary) {
	// 1. Calculate Standard Encoding (Baseline)
	const stdBinary = toBinaryUtf8(payload);
	const stdLength = 1 + stdBinary.length; // '0' + bits

	// 2. Setup for DP
	const n = payload.length;
	const MAX_DICT_INDEX = dictionary.length;

	// Optimization: Precompute global max match len
	let GLOBAL_MAX_MATCH = 0;
	for (const s of dictionary)
		if (s.length > GLOBAL_MAX_MATCH) GLOBAL_MAX_MATCH = s.length;

	// Optimization: Precompute byte offsets for O(1) literal cost calculation
	// byteOffsets[i] = number of bytes in payload.substring(0, i)
	const byteOffsets = new Int32Array(n + 1);
	let byteCount = 0;
	for (let i = 0; i < n; i++) {
		byteOffsets[i] = byteCount;
		let c = payload.codePointAt(i);
		if (c > 0xffff) {
			byteCount += 4;
			i++;
			byteOffsets[i] = byteCount;
		} // handle surrogate in loop?
		// Note: Simple indexing is tricky with surrogates.
		// For safety/speed balance on normal text, we'll re-calculate byte length for the literal
		// or just use a helper if MAX_LITERAL_LEN is small (250).
		// Since 250 is small, calling getUtf8ByteLength on substring is fast enough ($O(250)$).
	}

	// Precompute Matches
	const matches = new Map();
	for (let i = 0; i < n; i++) {
		const char = payload[i];
		const matchesAtI = [];
		for (let j = 0; j < dictionary.length; j++) {
			const txt = dictionary[j];
			let start = txt.indexOf(char);
			while (start !== -1) {
				let matchLen = 1;
				while (
					matchLen <
						Math.min(GLOBAL_MAX_MATCH, n - i, txt.length - start) &&
					payload[i + matchLen] === txt[start + matchLen]
				) {
					matchLen++;
				}
				// Only consider matches that are likely to save bits (>2 chars)
				if (matchLen > 2) {
					matchesAtI.push({ doc: j, idx: start, len: matchLen });
				}
				start = txt.indexOf(char, start + 1);
			}
		}
		if (matchesAtI.length) matches.set(i, matchesAtI);
	}

	const dp = Array(n + 1).fill(Infinity);
	const choice = Array(n).fill(null);
	dp[n] = 0;

	const BIT_WIDTH_LITERAL_LEN = getBitWidth(MAX_LITERAL_LEN);
	const BIT_WIDTH_DICT_IDX = getBitWidth(MAX_DICT_INDEX);
	const BIT_WIDTH_MATCH_LEN = getBitWidth(GLOBAL_MAX_MATCH);

	for (let i = n - 1; i >= 0; i--) {
		// Option A: Literal
		// Try reasonably sized chunks for literals to reduce loop overhead
		// We only check 1..MAX_LITERAL_LEN
		const maxL = Math.min(MAX_LITERAL_LEN, n - i);

		// Optimization: Just check a few specific lengths or step?
		// No, we need optimal. But we can optimize cost calc.
		for (let L = 1; L <= maxL; L++) {
			// Cost = Flag(1) + LenBits + (Bytes * 8)
			// We calculate bytes on the fly for small L
			const subStr = payload.substr(i, L);
			const byteLen = getUtf8ByteLength(subStr);

			const cost = 1 + BIT_WIDTH_LITERAL_LEN + byteLen * 8 + dp[i + L];
			if (cost < dp[i]) {
				choice[i] = { kind: "literal", len: L, subStr }; // cache subStr to avoid re-slicing later
				dp[i] = cost;
			}
		}

		// Option B: Dictionary
		const mList = matches.get(i);
		if (mList) {
			for (const m of mList) {
				// Cost = Flag(1) + DocIdxBits + OffsetBits + LenBits
				const docLenBits = getBitWidth(dictionary[m.doc].length);
				const cost =
					1 +
					BIT_WIDTH_DICT_IDX +
					docLenBits +
					BIT_WIDTH_MATCH_LEN +
					dp[i + m.len];

				if (cost < dp[i]) {
					choice[i] = { kind: "dict", ...m };
					dp[i] = cost;
				}
			}
		}
	}

	// Reconstruct Dictionary Path
	let currI = 0;
	let dictBinary = "";
	const references = [];

	while (currI < n) {
		const ch = choice[currI] || {
			kind: "literal",
			len: 1,
			subStr: payload[currI]
		};
		// Guard: Ensure len is always positive to prevent infinite loop
		const safeLen = Math.max(1, ch.len || 1);
		
		if (ch.kind === "literal") {
			// Re-calc binary here only for selected chunks
			const bin = toBinaryUtf8(ch.subStr || payload.substr(currI, safeLen));
			dictBinary += "0" + encodeInt(safeLen, MAX_LITERAL_LEN) + bin;
			references.push({ doc: null, idx: currI, len: safeLen });
		} else {
			dictBinary +=
				"1" +
				encodeInt(ch.doc, MAX_DICT_INDEX) +
				encodeInt(ch.idx, dictionary[ch.doc].length) +
				encodeInt(safeLen, GLOBAL_MAX_MATCH);
			references.push({ doc: ch.doc, idx: ch.idx, len: safeLen });
		}
		currI += safeLen;
	}

	const dictLength = 1 + dictBinary.length; // '1' + bits

	// 3. Efficiency Check & Selection
	// If Dictionary method is worse or equal to Standard, use Standard.
	// "way less than 1" efficiency logic interpreted as: if it doesn't compress, don't use it.

	if (dictLength >= stdLength) {
		return {
			method: "standard",
			payload,
			compressed: "0" + stdBinary, // Prepended '0'
			compressedLength: stdLength,
			originalLength: stdBinary.length,
			ratio: stdLength / (stdBinary.length || 1), // > 1 due to flag
			references: []
		};
	} else {
		return {
			method: "dictionary",
			payload,
			compressed: "1" + dictBinary, // Prepended '1'
			compressedLength: dictLength,
			originalLength: stdBinary.length,
			ratio: dictLength / (stdBinary.length || 1),
			references
		};
	}
}

/* ============================================================
   EMBEDDING LOGIC (Unchanged but utilizes optimized Inputs)
   ============================================================ */

function embedInCommentSelection(bits, post) {
	const comments = post?.comments || [];
	const flattenedComments = flattenComments(comments);
	const n = flattenedComments.length;
	const bitsCount = getBitWidth(n);
	const { bitsUsed, remaining, insufficient } = takeBits(bits, bitsCount);

	let selectionIndex = parseInt(bitsUsed || "0", 2) || 0;
	if (selectionIndex > n) selectionIndex = selectionIndex % (n + 1);

	let pickedCommentChain = [];
	if (selectionIndex > 0 && n > 0) {
		const pickedComment = flattenedComments[selectionIndex - 1];
		const commentMap = new Map();
		for (const c of flattenedComments) commentMap.set(c.id, c);

		let current = pickedComment;
		const visitedIds = new Set(); // Guard: Track visited comments to prevent circular references
		while (current) {
			// Guard: Break if we've already visited this comment (circular reference)
			if (visitedIds.has(current.id)) break;
			visitedIds.add(current.id);
			
			pickedCommentChain.unshift({
				name: current.author || "Unknown",
				body: current.body || "",
				id: current.id,
				parent_id: current.parent_id,
				permalink: current.permalink
			});
			if (current.parent_id === current.link_id) break;
			const parent = commentMap.get(current.parent_id);
			if (!parent || parent === current) break;
			current = parent;
		}
	}

	return {
		result: {
			bitsUsed,
			bitsCount,
			targetType: selectionIndex === 0 ? "post" : "comment",
			context: {
				id: post?.id,
				title: post?.title,
				author: post?.author,
				permalink: post?.permalink
			},
			pickedCommentChain,
			insufficientBits: insufficient
		},
		remainingBits: remaining
	};
}

function embedInAngleSelection(bits, nestedAngles, documents) {
	const angles = nestedAngles.flat().filter(Boolean);
	if (angles.length === 0) {
		return {
			bitsUsed: "",
			remainingBits: bits,
			selectedAngle: {},
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

	// Find source doc
	// Optimization: Don't re-map everything if we just need the source index
	// But we need to find which document contains this specific angle object
	let sourceDocIdx = -1;
	outerLoop: for (let d = 0; d < nestedAngles.length; d++) {
		const docAngles = nestedAngles[d];
		if (!Array.isArray(docAngles)) continue;
		for (const a of docAngles) {
			if (
				a.source_quote === selectedAngle.source_quote &&
				a.tangent === selectedAngle.tangent
			) {
				sourceDocIdx = d;
				break outerLoop;
			}
		}
	}

	const sourceDoc =
		sourceDocIdx >= 0 && sourceDocIdx < documents.length
			? documents[sourceDocIdx]
			: null;

	// Perform Search
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
		totalAnglesSelectedFirst: [selectedAngle, ...remainingAngles],
		snippet,
		selectedSourceDocumentIndex: sourceDocIdx,
		selectedSourceDocument: sourceDoc,
		insufficientBits: insufficient
	};
}

/* ============================================================
   MAIN
   ============================================================ */

const results = $input.all().map((item) => {
	const warnings = [];

	let data = item.json;
	if (data?.angles && data?.data) data = { ...data.data, angles: data.angles };
	const post = data?.post || data || {};

	let payload = $("SetSecretData").first()?.json?.payload;
	if (payload?.payload) payload = payload.payload;

	if (!isNonEmptyString(payload)) {
		return { json: { error: "No payload", warnings: ["Invalid payload"] } };
	}

	// Step 3: Angles
	const nestedAngles = (post?.angles || data?.angles || [])
		.filter(Boolean)
		.map((x) => (Array.isArray(x) ? x.filter(Boolean) : [x]));

	// Step 4: Dictionary & Compress
	const dictionary = buildDictionary(post);
	const compression = compressPayload(payload, dictionary);

	if (compression.method === "standard") {
		warnings.push(
			"Dictionary compression inefficient; used standard encoding."
		);
	}

	// Step 5: Embed Comments
	const commentEmb = embedInCommentSelection(compression.compressed, post);
	if (commentEmb.result.insufficientBits)
		warnings.push("Padding used in Comment Selection.");

	// Step 6: Embed Angles
	const documents = Array.isArray(post?.search_results)
		? post.search_results
		: [];
	const angleEmb = embedInAngleSelection(
		commentEmb.remainingBits,
		nestedAngles,
		documents
	);
	if (angleEmb.insufficientBits)
		warnings.push("Padding used in Angle Selection.");

	return {
		json: {
			compression,
			commentEmbedding: commentEmb.result,
			angleEmbedding: angleEmb,
			totalBitsEmbedded: commentEmb.result.bitsCount + angleEmb.bitsCount,
			fullEncodedBits: commentEmb.result.bitsUsed + angleEmb.bitsUsed,
			warnings
		}
	};
});

return results;
