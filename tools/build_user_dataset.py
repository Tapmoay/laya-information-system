"""把 USER 标签与打分合成拟合数据集（一次性脚本，结果落 calibration/dataset-user.jsonl）。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

labels = [json.loads(l) for l in Path("calibration/labels-user-ai.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
user = {row["item_id"]: row for row in labels if row.get("labeler") == "USER"}

scores = {}
for path in Path("calibration").glob("scores-*.jsonl"):
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        scores.setdefault(row["item_id"], row)

rows = []
for item_id, label_row in user.items():
    row = scores.get(item_id)
    if row is None:
        continue
    rows.append(
        {
            "item_id": item_id,
            "profile_id": "user",
            "scores": row["scores"],
            "label": int(label_row["label"]),
            "provenance": {"labeler": "USER", "note": label_row.get("note", ""), "method": label_row.get("method")},
        }
    )

Path("calibration/dataset-user.jsonl").write_text(
    "".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in rows), encoding="utf-8"
)
positives = sum(row["label"] for row in rows)
print("合成 {} 行，正例 {} / 负例 {}".format(len(rows), positives, len(rows) - positives))
