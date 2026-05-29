from huggingface_hub import snapshot_download

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
OUT = snapshot_download(repo_id=MODEL, resume_download=True)
print(OUT)
