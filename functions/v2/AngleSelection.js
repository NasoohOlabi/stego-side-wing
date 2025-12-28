/**
 * Calculates the Levenshtein distance between two strings.
 * The distance is the minimum number of single-character edits
 * (insertions, deletions or substitutions) required to change
 * one string into the other.
 *
 * @param {string} str1 – First string.
 * @param {string} str2 – Second string.
 * @returns {number} – Edit distance ≥ 0.
 */
function levenshteinDistance(str1, str2) {
	const matrix = [];

	// Initialise first column
	for (let i = 0; i <= str2.length; i++) matrix[i] = [i];
	// Initialise first row
	for (let j = 0; j <= str1.length; j++) matrix[0][j] = j;

	// Fill the matrix
	for (let i = 1; i <= str2.length; i++) {
		for (let j = 1; j <= str1.length; j++) {
			if (str2[i - 1] === str1[j - 1]) {
				matrix[i][j] = matrix[i - 1][j - 1];
			} else {
				matrix[i][j] = Math.min(
					matrix[i - 1][j - 1] + 1, // substitution
					matrix[i][j - 1] + 1, // insertion
					matrix[i - 1][j] + 1 // deletion
				);
			}
		}
	}
	return matrix[str2.length][str1.length];
}

/**
 * Performs a fuzzy search for `shortStr` inside `longStr` and returns the
 * first match surrounded by `n` characters of context on each side.
 * Matching tolerates up to `maxDistance` edit errors.
 *
 * @param {string} longStr – Text to search in.
 * @param {string} shortStr – Pattern to look for (case-insensitive).
 * @param {number} n – Amount of context (chars) to include before and after.
 * @param {number} [maxDistance=2] – Maximum allowed edit distance.
 * @returns {string|null} – Matching substring with context, or `null`.
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

/* ------------------------------------------------------------------ */
/* n8n entry-point                                                    */
/* ------------------------------------------------------------------ */

// Extract inputs
const payload = $("Text Based Compression").first().json.compressed;
const nestedAngles = $("Get Post")
	.first()
	.json.angles.filter(Boolean)
	.map((x) => x.filter(Boolean));
const angles = nestedAngles.flat();

// Utility: deep equality for angle objects
const eq = (a, b) =>
	a.source_quote === b.source_quote &&
	a.tangent === b.tangent &&
	a.category === b.category;

/* ---------- decode variable-length index prefix ---------- */
let n = -1;
for (let i = 1; i <= payload.length; i++) {
	if (parseInt(payload.substring(0, i), 2) >= angles.length) {
		n = i - 1;
		break;
	}
}
const bitsNeededForAngles = n;
const encoded = payload.substring(0, bitsNeededForAngles);
const remaining = payload.substring(bitsNeededForAngles);

const idx = parseInt(encoded, 2);
const selectedAngle = angles[idx];
const remainingAngles = angles.filter((_, xIdx) => xIdx !== idx);

/* ---------- tag every angle with its document index ---------- */
const totalAnglesSelectedFirst = [selectedAngle, ...remainingAngles].map(
	(angle) => ({
		...angle,
		source_document: nestedAngles.findIndex((docAngles) =>
			docAngles.some((o) => eq(o, angle))
		)
	})
);

/* ---------- build output ---------- */
return [
	{
		encoded,
		remaining,
		selectedAngle,
		remainingAngles,
		totalAnglesSelectedFirst,
		snippet: fuzzySearchWithContext(
			$("KeywordsSetsGeneration").last().json.dictionary[
				totalAnglesSelectedFirst[0].source_document
			],
			selectedAngle.source_quote,
			1000,
			20
		)
	}
];
