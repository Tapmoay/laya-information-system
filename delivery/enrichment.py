"""简报增强：中文标题、摘要、分方向相关度、可信度与推荐理由（CAP-DEL-05）。

这是 **AI 生成的表述**，不是证据：
- 每条都带 generated_by 与 not_evidence；
- 只允许出现在呈现层，不得回写测量或参与选择；
- 原始标题与链接始终保留。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEVELS = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
REQUIRED = {
    "candidate_id",
    "headline_zh",
    "summary_zh",
    "relevance_by_direction",
    "credibility",
    "why_recommended",
}


def load(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(payload: dict[str, Any], candidate_ids: list[str]) -> list[str]:
    problems: list[str] = []
    entries = payload.get("entries") or []
    seen = set()
    for entry in entries:
        missing = REQUIRED - set(entry)
        if missing:
            problems.append(f"{entry.get('candidate_id', '?')}: 缺少 {sorted(missing)}")
            continue
        cid = entry["candidate_id"]
        seen.add(cid)
        if entry["credibility"].get("level") not in LEVELS:
            problems.append(f"{cid}: credibility.level 不合法")
        for item in entry["relevance_by_direction"]:
            if item.get("level") not in LEVELS:
                problems.append(f"{cid}: {item.get('direction')} 的 level 不合法")
    missing_ids = [cid for cid in candidate_ids if cid not in seen]
    if missing_ids:
        problems.append(f"以下候选没有增强内容：{missing_ids}")
    return problems


def index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["candidate_id"]: entry for entry in payload.get("entries") or []}
