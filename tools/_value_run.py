import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
sys.path.insert(0, "models/_tf5")
from ingest.fetch import FetchPolicy, fetch
from judgment.questions import build_questions, to_engine_questions
from judgment.scoring import rank, select, summarise
from judgment.text import prepare
from strands_decider.infer import load_engine

engine = load_engine("models/strands-decider-2b-local", device="cuda")
policy = FetchPolicy(allowed_hosts=("www.qbitai.com", "www.infoq.cn"), timeout_seconds=15, max_attempts=1)
demand = json.load(open("demand/demand-2026-01-01.json", encoding="utf-8"))
questions = to_engine_questions(build_questions(demand))
first = json.load(open("runs/real-v2/fetch-results.json", encoding="utf-8"))
extra = json.load(open("runs/real-v2/gate-extra.json", encoding="utf-8"))
items = [{"title": r["title"], "text": r.get("text") or ""} for r in first]
items += [{"title": r["title"], "text": fetch(r["url"], request_id="val-1", policy=policy).get("text") or ""} for r in extra]
results = []
for item in items:
    body = prepare(item["text"], item["title"])["text"]
    answers = engine.ask(body, questions).answers
    raw = {n: ({"noul": answers[n].noul} if hasattr(answers[n], "noul") else {"probabilities": dict(answers[n].probabilities)}) for n in answers}
    s = summarise(raw); s["title"] = item["title"]; results.append(s)
picked = select(rank(results))
print("问题数:", len(questions))
for i, it in enumerate(picked["core"] + picked["exploration"], 1):
    print(f"{i}. {'核' if it in picked['core'] else '探'} {it['score']:.3f} 价值={it['value_max']:.2f} 方向={it['direction_max']:.2f} 质量={it['quality']:.2f} {it['form']:<9} {it['title'][:22]}")
print("底:", [f"{it['score']:.2f} {it['title'][:8]}" for it in picked["rest"][-3:]])
json.dump(results, open("runs/real-v2/value-scores.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
