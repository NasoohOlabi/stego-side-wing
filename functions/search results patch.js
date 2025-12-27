const get_original = false;

const deepClone = (value) => JSON.parse(JSON.stringify(value));

const extractFromResult = (result) =>
	get_original
		? result.fetched_content || result.content_analysis || result.snippet
		: result.content_analysis || result.fetched_content || result.snippet;

const normalizeResults = (res) => {
	// If it's an array of strings (including empty array), it's acceptable
	if (Array.isArray(res) && res.every((x) => typeof x === "string")) {
		return res;
	}
	if (Array.isArray(res) && res.length > 0 && res[0]?.json?.content) {
		return res.map((x) => x.json.content);
	}

	if (typeof res === "object" && Object.values(res).length > 0) {
		const flat = Object.values(res).flat();

		if (flat[0]?.json?.content) {
			return flat.map((x) => x.json.content);
		}

		if (
			flat[0]?.content_analysis ||
			flat[0]?.fetched_content ||
			flat[0]?.snippet
		) {
			return flat.map(extractFromResult).filter(Boolean);
		}

		// New case: the object maps strings to nested objects with embedded fields
		if (
			flat[0]?.link &&
			(flat[0]?.content_fetched !== undefined || flat[0]?.snippet)
		) {
			return flat
				.map(
					(result) =>
						result.content_analysis ||
						result.fetched_content ||
						result.snippet
				)
				.filter(Boolean);
		}
	}

	console.error("Unsupported search_results format", {
		type: typeof res,
		sample: res
	});
	throw new Error("Unsupported search_results format");
};

const processedItems = $input.all().map((item) => {
	const clonedItem = {
		...item,
		json: deepClone(item.json)
	};
	try {
		clonedItem.json.search_results = normalizeResults(
			clonedItem.json.search_results
		);
	} catch (error) {
		console.error("Failed to normalize search_results for item", {
			id: clonedItem.id,
			error: error.message
		});
		throw error;
	}

	return clonedItem;
});

return processedItems;
