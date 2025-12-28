// This will process every item currently in the n8n input stream
return $input.all().map((item, index) => {
	// 1. Extract Inputs for the CURRENT item
	// We use .json to access the data of the current item being iterated
	const payload = $("SetSecretData").first().json.payload.payload;

	// NOTE: If 'WrapPost' is a previous node that only has ONE item (like a config),
	// keep .first(). If it has multiple matching items, you might want to match by index.
	const scrapeData = $("WrapPost").first().json;

	// Safety check: if payload is missing, return the item as is or with an error
	if (!payload) return { json: { ...item.json, error: "No payload found" } };

	// ---------------------------------------------------------------------
	// 2. Build Dictionary
	// ---------------------------------------------------------------------
	const searchResults = scrapeData.search_results || [];
	const flattenComments = (comments) =>
		(comments || []).flatMap((c) => [c, ...flattenComments(c.replies || [])]);
	const comments = flattenComments(scrapeData.comments);

	const dictionary = [
		scrapeData.selftext,
		...searchResults,
		...comments
	].filter((x) => typeof x === "string" && x.length > 0);

	// ---------------------------------------------------------------------
	// 3. Compression Logic (Helpers)
	// ---------------------------------------------------------------------
	const getBitWidth = (max) => (max <= 1 ? 1 : Math.ceil(Math.log2(max)));

	const toBinaryUtf8 = (str) => {
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
	};

	const encodeInt = (n, max) => n.toString(2).padStart(getBitWidth(max), "0");

	// ---------------------------------------------------------------------
	// 4. Main Algorithm
	// ---------------------------------------------------------------------
	const n = payload.length;
	const MAX_LITERAL_LEN = 250;
	const MAX_DICT_INDEX = dictionary.length;
	const MAX_MATCH_LEN = dictionary.reduce(
		(max, s) => Math.max(max, s.length),
		0
	);

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

	const dp = Array(n + 1).fill(+Infinity);
	const choice = Array(n).fill(null);
	dp[n] = 0;

	for (let i = n - 1; i >= 0; i--) {
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
	let references = [];

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

	// Return the result in the standard n8n JSON format
	return {
		json: {
			payload,
			compressed: finalCompressed,
			compressedLength: finalCompressed.length,
			originalLength: toBinaryUtf8(payload).length,
			usedDict,
			ratio: finalCompressed.length / (toBinaryUtf8(payload).length || 1)
		}
	};
});
