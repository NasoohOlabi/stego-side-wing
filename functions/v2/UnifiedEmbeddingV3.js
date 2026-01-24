/**
 * UnifiedEmbeddingV3_Optimized.js
 *
 * High-performance single n8n Code node that performs two-level steganographic
 * embedding.
 *
 * UPDATES:
 * - embedInAngleSelection now selects a SET of angles by iteratively consuming bits.
 */

/* ============================================================
   CONFIG
   ============================================================ */

const MAX_LITERAL_LEN = 250;

// NEW: How many angles do you want to select?
// Set to 0 or null to auto-select based on data length (dynamic).
// Set to a number (e.g., 3) to force selecting exactly that many.
const TARGET_ANGLE_COUNT = $('SetSecretData').first().json.TARGET_ANGLE_COUNT;
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
    console.log("[buildDictionary] Starting dictionary building...");
    if (!scrapeData || typeof scrapeData !== "object") return [];
    const searchResults = Array.isArray(scrapeData.search_results)
        ? scrapeData.search_results
        : [];
    const comments = flattenComments(scrapeData.comments || []);
    const dict = [
        scrapeData.selftext,
        ...searchResults,
        ...comments.map((c) => c.body)
    ].filter(isNonEmptyString);
    console.log(
        `[buildDictionary] Dictionary built with ${dict.length} entries`
    );
    return dict;
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
    console.log(
        `[compressPayload] Starting compression for payload length: ${payload.length}, dictionary size: ${dictionary.length}`
    );
    // 1. Calculate Standard Encoding (Baseline)
    console.log("[compressPayload] Step 1: Calculating standard encoding...");
    const stdBinary = toBinaryUtf8(payload);
    const stdLength = 1 + stdBinary.length; // '0' + bits
    console.log(`[compressPayload] Standard encoding length: ${stdLength}`);

    // 2. Setup for DP
    const n = payload.length;
    const MAX_DICT_INDEX = dictionary.length;
    console.log(
        `[compressPayload] Step 2: Setting up DP for payload length ${n}...`
    );

    // Optimization: Precompute global max match len
    console.log(
        "[compressPayload] Step 2.1: Computing global max match length..."
    );
    let GLOBAL_MAX_MATCH = 0;
    for (const s of dictionary)
        if (s.length > GLOBAL_MAX_MATCH) GLOBAL_MAX_MATCH = s.length;
    console.log(
        `[compressPayload] Global max match length: ${GLOBAL_MAX_MATCH}`
    );

    // Optimization: Precompute byte offsets for O(1) literal cost calculation
    const byteOffsets = new Int32Array(n + 1);
    let byteCount = 0;
    for (let i = 0; i < n; i++) {
        byteOffsets[i] = byteCount;
        let c = payload.codePointAt(i);
        if (c > 0xffff) {
            byteCount += 4;
            i++;
            byteOffsets[i] = byteCount;
        }
    }

    // Precompute Matches
    console.log(
        "[compressPayload] Step 2.2: Precomputing matches (this may take a while)..."
    );
    const matches = new Map();
    for (let i = 0; i < n; i++) {
        if (i % 100 === 0 || i === n - 1) {
            console.log(
                `[compressPayload] Precomputing matches: ${
                    i + 1
                }/${n} (${Math.round(((i + 1) / n) * 100)}%)`
            );
        }
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

    console.log("[compressPayload] Step 2.3: Running DP algorithm...");
    const dp = Array(n + 1).fill(Infinity);
    const choice = Array(n).fill(null);
    dp[n] = 0;

    const BIT_WIDTH_LITERAL_LEN = getBitWidth(MAX_LITERAL_LEN);
    const BIT_WIDTH_DICT_IDX = getBitWidth(MAX_DICT_INDEX);
    const BIT_WIDTH_MATCH_LEN = getBitWidth(GLOBAL_MAX_MATCH);

    for (let i = n - 1; i >= 0; i--) {
        if ((n - i) % 50 === 0 || i === 0) {
            console.log(
                `[compressPayload] DP progress: ${n - i}/${n} (${Math.round(
                    ((n - i) / n) * 100
                )}%)`
            );
        }
        // Option A: Literal
        const maxL = Math.min(MAX_LITERAL_LEN, n - i);

        for (let L = 1; L <= maxL; L++) {
            const subStr = payload.substr(i, L);
            const byteLen = getUtf8ByteLength(subStr);

            const cost = 1 + BIT_WIDTH_LITERAL_LEN + byteLen * 8 + dp[i + L];
            if (cost < dp[i]) {
                choice[i] = { kind: "literal", len: L, subStr };
                dp[i] = cost;
            }
        }

        // Option B: Dictionary
        const mList = matches.get(i);
        if (mList) {
            for (const m of mList) {
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
    console.log("[compressPayload] DP algorithm completed");

    // Reconstruct Dictionary Path
    console.log("[compressPayload] Step 2.4: Reconstructing dictionary path...");
    let currI = 0;
    let dictBinary = "";
    const references = [];

    while (currI < n) {
        const ch = choice[currI] || {
            kind: "literal",
            len: 1,
            subStr: payload[currI]
        };
        const safeLen = Math.max(1, ch.len || 1);

        if (ch.kind === "literal") {
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

    const dictLength = 1 + dictBinary.length;
    console.log(`[compressPayload] Dictionary encoding length: ${dictLength}`);

    if (dictLength >= stdLength) {
        return {
            method: "standard",
            payload,
            compressed: "0" + stdBinary,
            compressedLength: stdLength,
            originalLength: stdBinary.length,
            ratio: stdLength / (stdBinary.length || 1),
            references: []
        };
    } else {
        return {
            method: "dictionary",
            payload,
            compressed: "1" + dictBinary,
            compressedLength: dictLength,
            originalLength: stdBinary.length,
            ratio: dictLength / (stdBinary.length || 1),
            references
        };
    }
}

/* ============================================================
   EMBEDDING LOGIC
   ============================================================ */

function embedInCommentSelection(bits, post) {
    console.log(
        `[embedInCommentSelection] Starting comment embedding with ${bits.length} bits`
    );
    const comments = post?.comments || [];
    const flattenedComments = flattenComments(comments);
    const n = flattenedComments.length;
    console.log(`[embedInCommentSelection] Found ${n} flattened comments`);
    const bitsCount = getBitWidth(n);
    const { bitsUsed, remaining, insufficient } = takeBits(bits, bitsCount);
    console.log(
        `[embedInCommentSelection] Selected comment index: ${
            parseInt(bitsUsed || "0", 2) || 0
        }`
    );

    let selectionIndex = parseInt(bitsUsed || "0", 2) || 0;
    if (selectionIndex > n) selectionIndex = selectionIndex % (n + 1);

    let pickedCommentChain = [];
    if (selectionIndex > 0 && n > 0) {
        const pickedComment = flattenedComments[selectionIndex - 1];
        const commentMap = new Map();
        for (const c of flattenedComments) commentMap.set(c.id, c);

        let current = pickedComment;
        const visitedIds = new Set();
        while (current) {
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

            // --- FIX START: Handle Parent ID Lookup Mismatch ---
            let parent = commentMap.get(current.parent_id);
            if (!parent && typeof current.parent_id === 'string' && current.parent_id.includes('_')) {
                const strippedId = current.parent_id.split('_').pop();
                parent = commentMap.get(strippedId);
            }
            // --- FIX END ---

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

/**
 * UPDATED: Selects MULTIPLE angles based on remaining bits.
 * It iteratively consumes bits to pick angles from the available pool until
 * bits run out or angles run out.
 */
/**
 * UPDATED: Selects angles.
 * If targetCount > 0, it tries to select exactly that many (padding if needed).
 * If targetCount is 0, it selects just enough to fit the data.
 */
async function embedInAngleSelection(bits, nestedAngles, targetCount) {
    console.log(`[embedInAngleSelection] Target: ${targetCount}`);

    let availableAngles = [...nestedAngles.flat().filter(Boolean)];
    const selectedAngles = [];
    let currentBits = bits;
    let totalBitsUsedStr = "";
    let totalBitsCount = 0;
    let anyInsufficient = false;

    // MANDATORY: We loop until selectedAngles.length === targetCount
    // The only hard stop is if we physically run out of angles in the pool.
    while (selectedAngles.length < targetCount && availableAngles.length > 0) {
        
        const range = availableAngles.length;
        const bitsNeeded = Math.ceil(Math.log2(range));
        
        // Take bits; if currentBits is empty, takeBits automatically pads with "0"
        const { bitsUsed, remaining, insufficient } = takeBits(currentBits, bitsNeeded);
        
        if (insufficient) {
            anyInsufficient = true;
        }

        let idx = parseInt(bitsUsed || "0", 2) || 0;
        if (idx >= range) idx = idx % range;

        selectedAngles.push(availableAngles[idx]);
        availableAngles.splice(idx, 1);

        // Update tracking
        currentBits = remaining;
        totalBitsUsedStr += bitsUsed;
        totalBitsCount += bitsNeeded;
    }

    return {
        bitsUsed: totalBitsUsedStr,
        bitsCount: totalBitsCount,
        remainingBits: currentBits, // If > 0, the payload was too big for the Target Count
        selectedAngles: selectedAngles,
        unselectedAngles: availableAngles,
        insufficientBits: anyInsufficient
    };
}

/* ============================================================
   MAIN
   ============================================================ */

const inputItems = $input.all();
console.log(`[MAIN] Starting processing of ${inputItems.length} item(s)`);

const results = await Promise.all(
    inputItems.map(async (item, itemIndex) => {
        const warnings = [];
        let data = item.json;
        if (data?.angles && data?.data) {
            data = { ...data.data, angles: data.angles };
        }
        const post = data?.post || data || {};

        let payload = $("SetSecretData").first()?.json?.payload;
        if (payload?.payload) payload = payload.payload;

        if (!isNonEmptyString(payload)) {
            return {
                json: { error: "No payload", warnings: ["Invalid payload"] }
            };
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

        // Step 6: Embed Angles (Pass the config constant)
        const angleEmb = await embedInAngleSelection(
            commentEmb.remainingBits,
            nestedAngles,
            TARGET_ANGLE_COUNT // <--- Pass the config here
        );

        if (angleEmb.insufficientBits)
            warnings.push("Padding used in Angle Selection.");
        
        // Warning logic:
        // If using Fixed Count, we might have leftover data if the count was too small.
        if (angleEmb.remainingBits.length > 0) {
            warnings.push(`Data truncated: ${angleEmb.remainingBits.length} bits could not be embedded (Limit of ${TARGET_ANGLE_COUNT || "available"} angles reached).`);
        }

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
    })
);

return results;