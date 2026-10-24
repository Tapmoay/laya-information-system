"""多画像标注集：同一条内容，对不同用户画像可能一个"要"一个"不要"。

每行记录一次人工判断：
{"item_id": "...", "profile_id": "ai", "scores": {问句名: {"noul": 0.7} 或 {"probabilities": {...}}}, "label": 1}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED = {"item_id", "profile_id", "scores", "label"}


def load(path: Path | str) -> list[dict[str, Any]]:
    rows = []
    # 允许带 BOM 的文件（Windows 记事本/PowerShell 常见）
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        problems = validate_row(row)
        if problems:
            raise ValueError(f"{row.get('item_id')}: {problems}")
        rows.append(row)
    return rows


def validate_row(row: dict[str, Any]) -> list[str]:
    problems = [f"缺少 {key}" for key in REQUIRED if key not in row]
    if "label" in row and row["label"] not in (0, 1):
        problems.append("label 必须是 0 或 1")
    if "scores" in row and not isinstance(row["scores"], dict):
        problems.append("scores 必须是对象")
    return problems


def profiles(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({row["profile_id"] for row in rows})
