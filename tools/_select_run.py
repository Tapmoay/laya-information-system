import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from judgment.scoring import rank, select

rows = json.load(open("runs/real-v2/value-scores.json", encoding="utf-8"))
picked = select(rank(rows))
for i, it in enumerate(picked["core"] + picked["exploration"], 1):
    bucket = "核心" if it in picked["core"] else "探索"
    print(f"{i}. [{bucket}] {it['score']:.3f} 价值={it['value_max']:.2f} 方向={it['direction_max']:.2f} 质量={it['quality']:.2f} {it['form']:<9} {it['title'][:24]}")
print("--- 剩余前 3 ---")
for it in picked["rest"][:3]:
    print(f"   {it['score']:.3f} {it['form']:<9} {it['title'][:26]}")
