import json
import os

files_to_delete = []
for file in os.listdir("datasets/news_researched"):
    # if the file is not a json file, skip it
    if not file.endswith(".json"):
        continue
    file_path = os.path.join("datasets/news_researched", file)
    file_content = None
    with open(file_path, "r", encoding="utf-8") as f:
        file_content = json.load(f)
    if file_content is None:
        continue
    # if the content is an object with only one key "data"
    if (
        isinstance(file_content, dict)
        and len(file_content) == 1
        and "data" in file_content
    ):
        file_content = file_content["data"]
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(file_content, f, indent=4)
        print(f"File {file} cleaned")
    elif len(file_content["search_results"]) == 0:
        files_to_delete.append(file_path)
        print(f"File {file} will be deleted")
    elif isinstance(file_content["search_results"], dict) and all(
        len(v) == 0 for v in file_content["search_results"].values()
    ):
        files_to_delete.append(file_path)
        print(f"File {file} will be deleted")

print(f"Files to delete: {files_to_delete}")
# loop over files in output-results
# if ['search_results'] is an empty list, delete the file
# files_to_delete = []
# for file in os.listdir("output-results"):
#     file_path = os.path.join("output-results", file)
#     if not file.endswith(".json"):
#         continue
#     with open(file_path, "r", encoding="utf-8") as f:
#         data = json.load(f)
#         if "search_results" in data and len(data["search_results"]) == 0:
#             files_to_delete.append(file)
#             print(f"File {file} deleted")

# print(f"Files to delete: {files_to_delete}")

# delete the files
for file in files_to_delete:
    os.remove(file)
    print(f"File {file} deleted")

# report the number of files deleted
print(f"Number of files deleted: {len(files_to_delete)}")
