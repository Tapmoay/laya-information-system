"""把「打分行」和「标签」拼成一份可以直接拟合的数据集。

为什么要单独一步：分数是模型算的，标签是人给的，两者天然属于不同时间、
不同文件、不同责任方。拼在一起的那一刻必须显式发生，
并且要能回答"这一行是从哪来的"。
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

from calibration.dataset import validate_row  # noqa: E402


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_labels(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    labels: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _jsonl(path):
        key = (row["item_id"], row["profile_id"])
        if key in labels and labels[key]["label"] != row["label"]:
            raise SystemExit(f"同一条内容被标了两次而且不一致：{key}")
        labels[key] = row
    return labels


def merge(parts: list[tuple[Path, Path]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    stats = {"rows": 0, "missing_label": 0, "duplicates": 0, "by_profile": {}}
    for scores_path, labels_path in parts:
        labels = load_labels(labels_path)
        for row in _jsonl(scores_path):
            key = (row["item_id"], row["profile_id"])
            label_row = labels.get(key)
            if label_row is None:
                stats["missing_label"] += 1
                continue
            if key in merged:
                stats["duplicates"] += 1
                continue
            out = {
                "item_id": row["item_id"],
                "profile_id": row["profile_id"],
                "scores": row["scores"],
                "label": int(label_row["label"]),
            }
            provenance = {**row.get("provenance", {})}
            for field in ("labeler", "method", "labeled_at", "domain"):
                if field in label_row:
                    provenance[field] = label_row[field]
            if provenance:
                out["provenance"] = provenance
            problems = validate_row(out)
            if problems:
                raise SystemExit(f"{key}: {problems}")
            merged[key] = out
            stats["by_profile"][row["profile_id"]] = stats["by_profile"].get(row["profile_id"], 0) + 1
    rows = sorted(merged.values(), key=lambda row: (row["profile_id"], row["item_id"]))
    stats["rows"] = len(rows)
    return rows, stats


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="拼接打分行与标签，产出可拟合数据集")
    parser.add_argument("--part", action="append", required=True, help="scores.jsonl:labels.jsonl")
    parser.add_argument("--out", default="calibration/dataset-v2.jsonl")
    args = parser.parse_args(argv)

    parts = []
    for spec in args.part:
        scores, _, labels = spec.partition(":")
        parts.append((Path(scores), Path(labels or scores)))
    rows, stats = merge(parts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in rows), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    print("写入", out, len(rows), "行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
