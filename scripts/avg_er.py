import json
import os

# loop over files in output-results

len_list = []

for file in os.listdir("output-results"):
    file_path = os.path.join("output-results", file)
    if file.startswith("2025"):
        continue
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        print(data)
        if "stegoText" not in data:
            continue
        print("stegoText ~ ", len(data["stegoText"]))
        len_list.append(len(data["stegoText"]))

print("Average length of stegoText ~ ", sum(len_list) / len(len_list))
