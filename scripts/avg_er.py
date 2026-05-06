import json
import os
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from infrastructure.process_tracking import append_current_pid_to_log

# loop over files in output-results

len_list = []

append_current_pid_to_log()

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
