import json, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "LayaResearch/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, resp.read()

for repo in [
    "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610",
    "convaiinnovations/laya",
]:
    try:
        status, body = get(f"https://hf-mirror.com/api/models/{repo}")
        data = json.loads(body)
        files = [s["rfilename"] for s in data.get("siblings", [])]
        print(repo, status, "files:", len(files))
        print("   ", [f for f in files if f.endswith((".safetensors", ".bin", ".json"))][:8])
    except Exception as exc:
        print(repo, "FAIL", type(exc).__name__, str(exc)[:120])
