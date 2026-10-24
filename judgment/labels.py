"""给一条内容打**目标标签**：它同时服务哪些目标。

为什么是多标签而不是单选：一条关于 agent 评测的论文，既服务长期主目标
（AI 系统的方法论），也直接服务当前任务（agent 工程落地）——这是事实，
不是测量误差。旧的单一通道（argmax）会把它压成一个标签，看起来像"只能是一边"。

标签是**描述**，不是选择规则：选不选仍然按分数和理由来，标签只负责让人看懂
"这条为什么值得看、服务的是哪一层目标"。

阈值 0.55 来自实测分布（2026-10-24，223 条）：
    0.45 → 多标签 62 条；0.50 → 50；0.55 → 41；0.60 → 35；0.65 → 22
取 0.55 与 channels.py 的 secondary_floor 一致，避免两处标准打架。
"""

from __future__ import annotations

from typing import Any

LONG_TERM = "长期主目标"
CURRENT_TASK = "当前任务"
VISUAL = "示例第二方向"
SECURITY = "安全探索"

DEFAULT_LABEL_THRESHOLD = 0.55

_SLOT_TO_LABEL = (
    ("primary", LONG_TERM),
    ("task", CURRENT_TASK),
    ("secondary", VISUAL),
)


def goal_labels(values: dict[str, Any] | None, *, threshold: float = DEFAULT_LABEL_THRESHOLD) -> list[str]:
    """按阈值给标签；可以一条都不给，也可以给多个。"""
    v = values or {}
    labels: list[str] = []
    for slot, label in _SLOT_TO_LABEL:
        try:
            score = float(v.get(slot) or 0.0)
        except (TypeError, ValueError):
            continue
        if score >= threshold:
            labels.append(label)
    return labels


def describe(labels: list[str]) -> str:
    return " · ".join(labels) if labels else "未命中任何目标"
