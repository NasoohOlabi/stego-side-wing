// Extract input data
const payload = $("Text Based Compression").first().json.compressed;
const keyword_sets = $("Logic").first().json.result.keyword_sets;

// ---------------------------------------------------------------------
// Keyword-based Encoder/Decoder
// ---------------------------------------------------------------------
class KeywordEncoder {
	constructor(keyword_sets) {
		this.keyword_sets = keyword_sets;
		// Process in exact order: subject → predicate → object → emotion
		this.categories = ["subject", "predicate", "object", "emotion"];
		this.category_sizes = this.categories.map((cat) =>
			keyword_sets[cat] ? keyword_sets[cat].length : 0
		);
	}

	// Calculate bits needed to represent a number
	static bitsNeeded(n) {
		return n <= 1 ? 1 : Math.ceil(Math.log2(n));
	}

	// Encode payload using keyword sets
	encode(payload) {
		let bitIndex = 0;
		const result = {
			encoded: [],
			used_bits: 0,
			original_payload: payload,
			leftover_payload: ""
		};

		// Process each category in exact order: subject → predicate → object → emotion
		for (let catIndex = 0; catIndex < this.categories.length; catIndex++) {
			const category = this.categories[catIndex];
			const keywords = this.keyword_sets[category];

			// Skip if category doesn't exist in keyword_sets
			if (!keywords || keywords.length === 0) {
				result.encoded.push({
					category,
					keyword: null,
					index: 0,
					bits_used: 0,
					bits: null
				});
				continue;
			}

			const bitsNeeded = KeywordEncoder.bitsNeeded(keywords.length);

			// Extract bits for this category
			if (bitIndex + bitsNeeded > payload.length) {
				// Not enough bits remaining, use default (index 0)
				result.encoded.push({
					category,
					keyword: keywords[0],
					index: 0,
					bits_used: 0,
					bits: null
				});
				continue;
			}

			const bits = payload.slice(bitIndex, bitIndex + bitsNeeded);
			const index = parseInt(bits, 2);

			// Ensure index is within bounds
			const safeIndex = Math.min(index, keywords.length - 1);

			result.encoded.push({
				category,
				keyword: keywords[safeIndex],
				index: safeIndex,
				bits_used: bitsNeeded,
				bits
			});

			bitIndex += bitsNeeded;
		}

		result.used_bits = bitIndex;
		result.leftover_payload = payload.slice(bitIndex);
		return result;
	}

	// Decode back to original payload
	decode(encoded) {
		let result = "";

		for (const item of encoded) {
			if (item.bits_used > 0) {
				// Reconstruct the original bits
				const bits = item.index.toString(2).padStart(item.bits_used, "0");
				result += bits;
			}
		}

		// Add leftover payload if it exists
		if (encoded.leftover_payload) {
			result += encoded.leftover_payload;
		}

		return result;
	}

	// Get compression statistics
	getStats(originalPayload, encoded) {
		const originalBits = originalPayload.length;
		const usedBits = encoded.used_bits;
		const leftoverBits = encoded.leftover_payload
			? encoded.leftover_payload.length
			: 0;
		const compressionRatio = usedBits / originalBits;

		return {
			original_bits: originalBits,
			used_bits: usedBits,
			leftover_bits: leftoverBits,
			compression_ratio: compressionRatio,
			bits_saved: originalBits - usedBits,
			categories_used: encoded.encoded.length
		};
	}
}

// ---------------------------------------------------------------------
// Run encoding
// ---------------------------------------------------------------------
const encoder = new KeywordEncoder(keyword_sets);
const encoded = encoder.encode(payload);
const decoded = encoder.decode(encoded.encoded);
const stats = encoder.getStats(payload, encoded);

// Validation
const validationPassed = decoded === payload;

return [
	{
		payload,
		keyword_sets,
		encoded,
		decoded,
		validationPassed,
		stats
	}
];
