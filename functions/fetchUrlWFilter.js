const items = Array.from($input.all())
	.filter((item) => {
		// 1. Setup allowed Content-Types
		const ALLOWED_TYPES = ["application/json", "text/html", "text/plain"];

		// 2. Extract and clean the Header
		const contentType = (
			item.json.headers?.["content-type"] || ""
		).toLowerCase();

		// Check if the content-type matches any of our allowed strings
		const hasValidHeader = ALLOWED_TYPES.some((type) =>
			contentType.includes(type)
		);
		if (!hasValidHeader) return false;

		// 3. Extract the Body Text
		const textContent = item.json.body?.result?.text;

		if (typeof textContent !== "string") return false; // Keep if no text to check

		// 4. Use Regex to check for binary signatures (PDF or PNG)
		// ^ matches the start of the string; | acts as an OR
		const binarySignatures = ["�PNG", "�PDF"];

		return !binarySignatures.some((x) => textContent.startsWith(x));
	})
	.map((item) => item.json.body.result);

if (items.length === 0) {
	throw new Error("No valid JSON, HTML, or Text content found.");
}

return items;
