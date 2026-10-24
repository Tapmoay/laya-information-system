"""把复核页导出的结果并成标签，并算出这次终选准不准。

为什么单独一步：复核结果是「人给的判据」，selection.json 是「Agent 给的决定」，
两者相撞才能得出对错。把这件事写成工具，是为了每次都能重算，而不是靠嘴说。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LABELS = {"要": 1, "不要": 0}


def _load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _flat_to_labels(data: dict[str, Any]) -> dict[str, int]:
    """标注页导出：{item_id: "要"} 或 {item_id: {"v": "要", "note": "..."}} 两种都认。"""
    rows = []
    for item_id, payload in data.items():
        verdict = payload if isinstance(payload, str) else (payload or {}).get("v")
        note = "" if isinstance(payload, str) else str((payload or {}).get("note") or "").strip()
        if verdict not in LABELS:
            continue
        row = {
            "item_id": item_id,
            "profile_id": "ai",
            "label": LABELS[verdict],
            "labeler": "USER",
            "method": "LABEL_PAGE",
        }
        if note:
            row["note"] = note
        rows.append(row)
    return rows


def evaluate(review: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    final = selection.get("selection", selection)
    picked = {entry["item_id"] for entry in final.get("core", []) + final.get("exploration", [])}
    excluded = {entry["item_id"] for entry in final.get("excluded", [])}

    rows: list[dict[str, Any]] = []
    for item_id, verdict in (review.get("picks") or {}).items():
        rows.append(
            {
                "item_id": item_id,
                "agent": "选中",
                "user": verdict,
                "agree": LABELS.get(verdict) == 1,
            }
        )
    for item_id, verdict in (review.get("exclusions") or {}).items():
        rows.append(
            {
                "item_id": item_id,
                "agent": "排除",
                "user": verdict,
                "agree": LABELS.get(verdict) == 0,
            }
        )

    decided = [row for row in rows if row["user"] in LABELS]
    abstain = [row for row in rows if row["user"] not in LABELS]
    picked_rows = [row for row in decided if row["agent"] == "选中"]
    excluded_rows = [row for row in decided if row["agent"] == "排除"]
    hits = sum(row["agree"] for row in decided)
    return {
        "total": len(rows),
        "abstain": len(abstain),
        "decided": len(decided),
        "agree": hits,
        "accuracy": round(hits / len(decided), 4) if decided else 0.0,
        "picks": {
            "count": len(picked_rows),
            "correct": sum(row["agree"] for row in picked_rows),
            "wrong_ids": [row["item_id"] for row in picked_rows if not row["agree"]],
        },
        "exclusions": {
            "count": len(excluded_rows),
            "correct": sum(row["agree"] for row in excluded_rows),
            "false_excludes": [row["item_id"] for row in excluded_rows if not row["agree"]],
        },
        "unjudged": sorted(picked ^ set(row["item_id"] for row in rows)),
        "rows": rows,
        "excluded_but_unrated": sorted(excluded - {row["item_id"] for row in rows}),
    }


def to_labels(review: dict[str, Any], *, profile_id: str = "ai") -> list[dict[str, Any]]:
    rows = []
    for verdict_map in (review.get("picks") or {}, review.get("exclusions") or {}):
        for item_id, verdict in verdict_map.items():
            if verdict not in LABELS:
                continue
            rows.append(
                {
                    "item_id": item_id,
                    "profile_id": profile_id,
                    "label": LABELS[verdict],
                    "labeler": "USER",
                    "method": "HTML_REVIEW",
                }
            )
    return rows


def merge_labels(path: Path, rows: list[dict[str, Any]]) -> int:
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                existing[record["item_id"]] = record
    for row in rows:
        existing[row["item_id"]] = {**existing.get(row["item_id"], {}), **row}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + chr(10) for record in existing.values()),
        encoding="utf-8",
    )
    return len(existing)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="合并复核结果并评估终选")
    parser.add_argument("--review", required=True)
    parser.add_argument("--selection", default=None, help="终选结果；--labels-only 时不需要")
    parser.add_argument("--labels", default="calibration/labels-user-ai.jsonl")
    parser.add_argument("--out", default=None, help="评估结果写到哪（默认与 selection 同目录）")
    parser.add_argument("--no-merge", action="store_true", help="只评估，不写标签")
    parser.add_argument("--labels-only", action="store_true",
                        help="输入是标注页导出的扁平表：只并标签，不评估终选")
    args = parser.parse_args(argv)

    review = _load(Path(args.review))
    if args.labels_only:
        rows = _flat_to_labels(review)
        if not rows:
            print("没有可用的判断（v 必须是 要/不要/说不准）", file=sys.stderr)
            return 2
        total = merge_labels(Path(args.labels), rows)
        print("读入 {} 条判断，标签文件现有 {} 条（labeler=USER）".format(len(rows), total))
        return 0
    selection = _load(Path(args.selection))
    result = evaluate(review, selection)

    out = Path(args.out) if args.out else Path(args.selection).parent / "evaluation.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")

    print("共判 {} 条（弃权 {} 条），一致 {} 条，准确率 {:.0%}".format(
        result["decided"], result["abstain"], result["agree"], result["accuracy"]))
    print("选中的 {} 条，判对 {} 条".format(result["picks"]["count"], result["picks"]["correct"]))
    print("排除的 {} 条，判对 {} 条，漏选 {} 条".format(
        result["exclusions"]["count"], result["exclusions"]["correct"],
        len(result["exclusions"]["false_excludes"])))
    for item_id in result["exclusions"]["false_excludes"]:
        print("  漏选:", item_id)
    if not args.no_merge:
        total = merge_labels(Path(args.labels), to_labels(review))
        print("标签文件现有 {} 条（labeler=USER）".format(total))
    print("写入", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())