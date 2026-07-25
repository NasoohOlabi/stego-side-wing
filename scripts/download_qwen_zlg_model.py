from huggingface_hub import snapshot_download

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
# `resume_download` was removed in huggingface_hub 1.x -- passing it raises TypeError.
# Downloads resume from the cache automatically now, so dropping it keeps the intent.
OUT = snapshot_download(repo_id=MODEL)
print(OUT)
