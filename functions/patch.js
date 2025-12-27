// Loop over input items and add a new field called 'myNewField' to the JSON of each one

const get_original = false;

for (const item of $input.all()) {
	const res = item.json.search_results;
	if (Array.isArray(res) && res.length > 0) {
		if (res[0]?.json?.content) {
			item.json.search_results = res.map((x) => x.json.content);
		}
	} else if (
		typeof res === "object" &&
		Array.isArray(Object.values(res)[0]) &&
		Object.values(res)[0][0].fetched_content &&
		Object.values(res)[0][0].content_analysis
	) {
		item.json.search_results = Object.values(res)
			.flat()
			.map((x) =>
				get_original
					? x.fetched_content || x.content_analysis || x.snippet
					: x.content_analysis || x.fetched_content || x.snippet
			)
			.filter((x) => !!x);
	} else {
		throw "New type patch me";
		return {
			o1: typeof res === "object",
			o2: Array.isArray(Object.values(res)[0]),
			o3: Object.values(res)[0].fetched_content,
			o4: Object.values(res)[0].content_analysis
		};
	}
}

return $input.all();
