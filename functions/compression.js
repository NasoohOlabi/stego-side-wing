const $input = require("./compressionInput.json");

// Extract input data
const payload = $input[0].payload.payload;
const datapoint = $input[1].result;
const postContent = datapoint.selftext ?? "";

// Flatten nested results/comments for easier processing
const searchResults = Object.values(datapoint.search_results || {}).flatMap(
	(x) => x.map((y) => y.fetched_content)
);

const flattenComments = (c) =>
	c.replies.length === 0 ? [c] : [c, ...c.replies.flatMap(flattenComments)];
const comments = (datapoint.comments || []).flatMap(flattenComments);

// Build dictionary of reference texts
const dictionary = [postContent, ...searchResults, ...comments].filter(
	(x) => typeof x === "string"
);

// ---------------------------------------------------------------------
// Compression utilities
// ---------------------------------------------------------------------
class TextCompressor {
	constructor(dictionary, opts = {}) {
		this.dictionary = dictionary.filter((x) => typeof x === "string");
		this.MAX_DICT_INDEX = this.dictionary.length;
		this.MAX_MATCH_LEN = this.dictionary
			.map((x) => x.length)
			.reduce((a, b) => Math.max(a, b), 0);
		this.MAX_LITERAL_LEN = opts.maxLiteralLen ?? 250;
		this.dictBinary = this.dictionary.map(TextCompressor.toBinaryUtf8);
	}

	// Convert string → binary UTF-8
	static toBinaryUtf8(str) {
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

	static intEncodedLen(n, max) {
		return max <= 1
			? 1
			: Math.max(
					Math.floor(Math.log2(n || 1)) + 1,
					Math.floor(Math.log2(max))
			  );
	}

	static encodeIntGreedy(n, max) {
		return max <= 1
			? "0"
			: n.toString(2).padStart(Math.floor(Math.log2(max)), "0");
	}

	// ---------------------------------------------------------------------
	// Compression
	// ---------------------------------------------------------------------
	compress(payload) {
		const n = payload.length;
		const dp = Array(n + 1).fill(Infinity);
		const choice = Array(n).fill(null);
		dp[n] = 0;

		// Precompute all possible matches for efficiency
		const matches = this.findAllMatches(payload);

		// Dynamic programming compression
		for (let i = n - 1; i >= 0; i--) {
			// Literal
			for (let L = 1; L <= Math.min(this.MAX_LITERAL_LEN, n - i); L++) {
				const cost =
					1 +
					TextCompressor.intEncodedLen(L, this.MAX_MATCH_LEN) +
					TextCompressor.toBinaryUtf8(payload.slice(i, i + L)).length +
					dp[i + L];
				if (cost < dp[i])
					(choice[i] = { kind: "literal", len: L }), (dp[i] = cost);
			}

			// Dictionary matches at position i
			const matchesAtI = matches.get(i) || [];
			for (const match of matchesAtI) {
				const cost =
					1 +
					TextCompressor.intEncodedLen(match.doc, this.MAX_DICT_INDEX) +
					TextCompressor.intEncodedLen(
						match.idx,
						this.dictionary[match.doc].length
					) +
					TextCompressor.intEncodedLen(match.len, this.MAX_MATCH_LEN) +
					dp[i + match.len];
				if (cost < dp[i])
					(choice[i] = {
						kind: "dict",
						doc: match.doc,
						len: match.len,
						idx: match.idx
					}),
						(dp[i] = cost);
			}
		}
		// Reconstruction
		let i = 0,
			compressed = "",
			references = [];
		while (i < n) {
			const ch = choice[i] ?? { kind: "literal", len: 1 };
			if (ch.kind === "literal") {
				const bits = TextCompressor.toBinaryUtf8(
					payload.slice(i, i + ch.len)
				);
				compressed +=
					"0" +
					TextCompressor.encodeIntGreedy(ch.len, this.MAX_MATCH_LEN) +
					bits;
				references.push({ doc: null, idx: i, len: ch.len });
			} else {
				compressed +=
					"1" +
					TextCompressor.encodeIntGreedy(ch.doc, this.MAX_DICT_INDEX) +
					TextCompressor.encodeIntGreedy(
						ch.idx,
						this.dictionary[ch.doc].length
					) +
					TextCompressor.encodeIntGreedy(ch.len, this.MAX_MATCH_LEN);
				references.push({ doc: ch.doc, idx: ch.idx, len: ch.len });
			}
			i += ch.len;
		}
		const usedDict = references.some((r) => r.doc !== null);

		// --- If no dictionary usage, simplify to "0" + original binary ---
		if (!usedDict) {
			const originalBits = TextCompressor.toBinaryUtf8(payload);
			return {
				compressed: "0" + originalBits, // just add 1 bit flag
				usedDict: false,
				references
			};
		}

		// Otherwise prepend flag and return
		compressed = "1" + compressed;
		return { compressed, usedDict: true, references };
	}

	// Find all possible matches between payload and dictionary
	findAllMatches(payload) {
		const matches = new Map();

		for (let i = 0; i < payload.length; i++) {
			const matchesAtI = [];

			// Check each dictionary entry
			for (let j = 0; j < this.dictionary.length; j++) {
				const dictText = this.dictionary[j];

				// Find all occurrences of payload substring starting at position i
				for (let k = 0; k < dictText.length; k++) {
					let matchLen = 0;
					while (
						matchLen <
							Math.min(
								this.MAX_MATCH_LEN,
								payload.length - i,
								dictText.length - k
							) &&
						payload[i + matchLen] === dictText[k + matchLen]
					) {
						matchLen++;
					}

					// Add matches of length 1 or more
					for (let L = 1; L <= matchLen; L++) {
						matchesAtI.push({
							doc: j,
							idx: k,
							len: L
						});
					}
				}
			}

			if (matchesAtI.length > 0) {
				matches.set(i, matchesAtI);
			}
		}

		return matches;
	}

	// ---------------------------------------------------------------------
	// Decompression
	// ---------------------------------------------------------------------
	decompress(compressed) {
		let i = 0;
		const modeFlag = compressed[i++]; // "0" => no dict used
		if (modeFlag === "0") {
			// just raw literal binary UTF-8
			const bits = compressed.slice(i);
			let str = "";
			for (let j = 0; j < bits.length; j += 8)
				str += String.fromCharCode(parseInt(bits.slice(j, j + 8), 2));
			return str;
		}
		let result = "";
		while (i < compressed.length) {
			const kind = compressed[i++];
			if (kind === "0") {
				const lenBits = TextCompressor.intEncodedLen(1, this.MAX_MATCH_LEN);
				const L = parseInt(compressed.slice(i, i + lenBits), 2);
				i += lenBits;

				const charBits = compressed.slice(i, i + L * 8);
				i += L * 8;
				for (let j = 0; j < charBits.length; j += 8)
					result += String.fromCharCode(
						parseInt(charBits.slice(j, j + 8), 2)
					);
			} else {
				const docBits = TextCompressor.intEncodedLen(
					1,
					this.MAX_DICT_INDEX
				);
				const docIdx = parseInt(compressed.slice(i, i + docBits), 2);
				i += docBits;
				const idxBits = TextCompressor.intEncodedLen(
					1,
					this.dictionary[docIdx].length
				);
				const idx = parseInt(compressed.slice(i, i + idxBits), 2);
				i += idxBits;

				const lenBits = TextCompressor.intEncodedLen(1, this.MAX_MATCH_LEN);
				const L = parseInt(compressed.slice(i, i + lenBits), 2);
				i += lenBits;

				result += this.dictionary[docIdx].slice(idx, idx + L);
			}
		}
		return result;
	}
}

// ---------------------------------------------------------------------
// Run compression
// ---------------------------------------------------------------------
const compressor = new TextCompressor(dictionary);
const { compressed, usedDict } = compressor.compress(payload);
const decoded = compressor.decompress(compressed);

return [
	{
		dictionary,
		payload,
		compressed,
		compressedLength: compressed.length,
		originalLength: TextCompressor.toBinaryUtf8(payload).length,
		usedDict,
		validationPassed: decoded === payload
	}
];
