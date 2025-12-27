import json
import os

# Collect IDs of files to delete (legacy behavior preserved)
files_to_delete = []
flattened_by_file = {}


"""
if (
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
	}
"""

get_original = False

# Iterate over all JSON files in the same directory
for file in os.listdir("datasets/news_researched"):
    file_path = os.path.join("datasets/news_researched", file)
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            print(f"Error: {file} is not a valid JSON file")
            content = f.read()
            print(content)
            print("-" * 100)
            if len(content) == 0:
                files_to_delete.append(file)
            continue

        # Flatten search_results into a List[str] under a new key
        flat_texts = []
        search_results = data.get("search_results")
        if isinstance(search_results, list):
            if len(search_results) == 0:
                # delete the file
                files_to_delete.append(file)
                print(f"File {file} deleted: search_results is empty")
                continue
            for entry in search_results:
                if isinstance(entry, dict):
                    # Case: entry.json.text (list of strings)
                    json_section = entry.get("json")
                    if isinstance(json_section, dict) and isinstance(
                        json_section.get("text"), list
                    ):
                        flat_texts.extend(json_section["text"])
                    # Case: entry.text (list of strings)
                    elif isinstance(entry.get("text"), list):
                        flat_texts.extend(entry["text"])
                    elif "fetched_content" in entry and "content_analysis" in entry:
                        if get_original:
                            flat_texts.append(
                                entry["fetched_content"]
                                or entry["content_analysis"]
                                or entry["snippet"]
                            )
                        else:
                            flat_texts.append(
                                entry["content_analysis"]
                                or entry["fetched_content"]
                                or entry["snippet"]
                            )

                elif isinstance(entry, str):
                    flat_texts.append(entry)
        elif isinstance(search_results, dict):
            # If search_results is a dict of lists, flatten all lists
            for v in search_results.values():
                if isinstance(v, list):
                    flat_texts.extend(v)

        data["search_results"] = flat_texts
        flattened_by_file[file] = flat_texts

        # Delete files missing search_results and preserve old behavior for empty dicts
        should_delete = False
        if "search_results" not in data:
            should_delete = True
        elif isinstance(data.get("search_results"), dict) and all(
            isinstance(v, list) and len(v) == 0 for v in data["search_results"].values()
        ):
            should_delete = True

        if should_delete:
            files_to_delete.append(file)
        else:
            # Write the flattened data back to the file
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Files to delete: {files_to_delete}")

# delete the files marked for deletion
for file in files_to_delete:
    os.remove(os.path.join("datasets/news_researched", file))
    print(f"File deleted: {file}")

# Report per-file flatten results (helps verify the transformation)
for fname, texts in flattened_by_file.items():
    print(f"{fname}: flattened {len(texts)} texts")


#  loop over "news_url_fetched" if the  json has empty search_results remove the search_results field

for file in os.listdir("datasets/news_url_fetched"):
    file_path = os.path.join("datasets/news_url_fetched", file)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        if "search_results" in data and len(data["search_results"]) == 0:
            del data["search_results"]
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"File {file} updated: search_results removed")
        elif (
            "search_results" in data
            and isinstance(data["search_results"], dict)
            and all(len(v) == 0 for v in data["search_results"].values())
        ):
            del data["search_results"]
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"File {file} updated: search_results removed")
