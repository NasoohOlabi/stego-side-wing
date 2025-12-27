import json
import os

dry_run = False
files_to_delete = []

for file in os.listdir("datasets/news_researched"):
    if "1loqk9w.json" in file:
        print(f"Processing file: {'👉🏻' if '1loqk9w.json' in file else ''}\t\t{file}")
    # if the file is not a json file, skip it
    if not file.endswith(".json"):
        print(f"File {file} is not a json file")
        continue
    file_path = os.path.join("datasets/news_researched", file)
    post = None
    with open(file_path, "r", encoding="utf-8") as f:
        post = json.load(f)
    if post is None:
        continue
    if "data" in post:
        post = post["data"]
    if "search_results" not in post:
        files_to_delete.append(file_path)
        print(f"File {file} will be deleted: search_results not found")
        continue
    search_results = post["search_results"]
    if isinstance(search_results, list):
        search_results = post["search_results"]
    elif isinstance(search_results, dict) and all(
        isinstance(item, list) for item in search_results.values()
    ):
        search_results = [
            item
            for sublist in search_results.values()
            for item in sublist
            if item != ""
        ]
    else:
        files_to_delete.append(file_path)
        print(f"File {file} will be deleted: search_results is not a list or dict")
        continue
    search_results = [item for item in search_results if item != ""]
    if len(search_results) == 0:
        files_to_delete.append(file_path)
        print(f"File {file} will be deleted: search_results is empty")
        continue
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(post, f, indent=4)
    print(f"File {file} edited")


if not dry_run:
    for file in files_to_delete:
        os.remove(file)
