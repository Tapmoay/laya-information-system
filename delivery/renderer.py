"""交付呈现（CAP-DEL-02）。

规则只有一条：**只能表达 DeliveryObject 里已经有的东西**。
不查来源、不补事实、不重新排序、不改推荐动作。
翻译尚未接入时，标题按原文显示，并在简报里如实标注。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

GROUP_LABELS = {
    "CORE": "核心信息",
    "EXPLORATION": "跨领域探索",
    "VISUAL_MATCH": "视觉匹配",
    "VISUAL_EXPLORATION": "视觉探索",
}

ACTION_LABELS = {
    "APPLY": "可直接应用",
    "STUDY": "值得学习",
    "VERIFY": "继续核验",
    "MONITOR": "持续观察",
    "SAVE": "暂存",
    "DISCARD": "舍弃",
}

COMPONENT_LABELS = {
    "TASK": "任务价值",
    "CAPABILITY": "能力价值",
    "DECISION": "决策价值",
    "EXPLORATION": "探索价值",
    "ENVIRONMENT": "环境价值",
}

VALUE_LABELS = {
    "DIRECT": "直接相关",
    "ADJACENT": "邻接相关",
    "EXPLORATORY": "探索性",
    "UNRELATED": "无关",
    "TRANSFERABLE_MECHANISM": "可迁移机制",
    "PRACTICE_OPPORTUNITY": "可练习",
    "GAP_FILLING": "填补能力缺口",
    "INCREMENTAL": "增量信息",
    "CHANGES_DECISION": "可能改变决策",
    "MAY_CHANGE_DECISION": "可能影响判断",
    "CONTEXT_ONLY": "只作背景",
    "HIGH": "高",
    "MEDIUM": "中",
    "MODERATE": "中",
    "LOW": "低",
    "NONE": "无",
    "ANNOUNCED": "仅公告",
    "DEMONSTRATED": "有演示",
    "REPRODUCIBLE": "可复现",
    "DEPLOYABLE": "可部署",
    "ESTABLISHED": "已确立",
    "SUPPORTED": "有证据支持",
    "PARTIALLY_SUPPORTED": "部分支持",
    "CONFLICTING": "证据冲突",
    "INSUFFICIENT_EVIDENCE": "证据不足",
}

EMPTY_LABELS = {
    "NO_ELIGIBLE_CANDIDATES": "本轮没有合格候选",
    "ALL_MEASUREMENTS_ABSTAINED": "所有候选的测量都弃权了",
    "CAPACITY_ZERO": "本轮容量为 0",
    "SOURCE_FAILURE": "来源全部失败",
}


def _explanation(decision: dict[str, Any] | None, measurements: dict[str, dict[str, Any]]) -> list[str]:
    """把记录下来的测量标签翻译成一句人话。

    只做翻译：每个短语都必须能对应到某条 measurement 的 label，
    不引入任何测量里没有的判断（测试会逐条核对）。
    """
    if not decision:
        return []
    lines: list[str] = []
    for component in decision.get("priority_components", []):
        label = None
        for ref in component.get("source_measurement_refs", []):
            measurement = measurements.get(ref)
            if measurement and measurement.get("value"):
                label = measurement["value"].get("label")
                break
        if not label:
            continue
        name = COMPONENT_LABELS.get(component["name"], component["name"])
        lines.append(f"{name}：{VALUE_LABELS.get(label, label)}")
    return lines


def _item_view(
    item: dict[str, Any],
    decision: dict[str, Any] | None = None,
    measurements: dict[str, dict[str, Any]] | None = None,
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    explanation = _explanation(decision, measurements or {})
    title = item.get("rendered_title") or item["original_title"]["text"]
    if enrichment and enrichment.get("headline_zh"):
        title = enrichment["headline_zh"]
    return {
        "candidate_id": item["candidate_id"],
        "priority_rank": item["priority_rank"],
        "grouping": item["grouping"],
        "original_title": item["original_title"],
        "displayed_title": title,
        "source_url": item["source_url"],
        "why": list(item.get("rationale_refs", [])),
        "caveats": list(item.get("required_caveats", [])),
        "action": item["recommended_action"],
        "explanation": explanation,
        "enrichment": enrichment or None,
    }


def _markdown(delivery: dict[str, Any], items: list[dict[str, Any]], rendered_at: datetime) -> str:
    lines = [
        f"# 个人信息简报 · {rendered_at.date().isoformat()}",
        "",
    ]

    empty_reason = delivery.get("empty_reason") or {}
    if not items:
        label = EMPTY_LABELS.get(empty_reason.get("code", ""), "本轮没有可交付内容")
        lines.append(f"> {label}。")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"> 本轮 {len(items)} 条 · 生成于 {rendered_at.isoformat(timespec='seconds')}")
    if any(item.get("enrichment") for item in items):
        lines.append("> 标题与摘要是 AI 生成的表述，不是证据；原文标题与链接始终保留。")
    elif delivery.get("delivery_language_mode") == "ZH_TRANSLATION":
        lines.append("> 译文尚未接入：标题按原文显示，原文链接始终保留。")
    lines.append("")

    by_group: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_group.setdefault(item["grouping"], []).append(item)

    for grouping, group_items in by_group.items():
        lines.append(f"## {GROUP_LABELS.get(grouping, grouping)}")
        lines.append("")
        for item in group_items:
            action = ACTION_LABELS.get(item["action"], item["action"])
            enriched = item.get("enrichment") or {}
            lines.append(f"{item['priority_rank']}. **{item['displayed_title']}** · {action}")
            lines.append(f"   [原文]({item['source_url']})")
            if enriched.get("summary_zh"):
                lines.append(f"   - 摘要：{enriched['summary_zh']}")
            directions = enriched.get("relevance_by_direction") or []
            if directions:
                parts = []
                for direction in directions:
                    level = VALUE_LABELS.get(direction["level"], direction["level"])
                    note = f"（{direction['note']}）" if direction.get("note") else ""
                    parts.append(f"{direction['direction']} {level}{note}")
                lines.append("   - 相关度：" + " · ".join(parts))
            credibility = enriched.get("credibility") or {}
            if credibility.get("level"):
                basis = f"——{credibility['basis']}" if credibility.get("basis") else ""
                lines.append(f"   - 可信度：{VALUE_LABELS.get(credibility['level'], credibility['level'])}{basis}")
            if enriched.get("why_recommended"):
                lines.append(f"   - 推荐理由：{enriched['why_recommended']}")
            if item["caveats"]:
                for caveat in item["caveats"]:
                    if enriched and "译文尚未生成" in caveat:
                        continue
                    lines.append(f"   - 注意：{caveat}")
            for reason in item.get("explanation", []):
                lines.append(f"   - {reason}")
            if item["why"]:
                lines.append(f"   - 依据：{', '.join(item['why'])}")
            if enriched:
                lines.append(f"   - 原文标题：{item['original_title']['text']}")
            lines.append("")

    return "\n".join(lines)


def render_brief(
    delivery: dict[str, Any],
    rendered_at: datetime,
    *,
    decisions: list[dict[str, Any]] | None = None,
    measurements: list[dict[str, Any]] | None = None,
    enrichment: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    decisions_by_candidate = {item["candidate_id"]: item for item in (decisions or [])}
    measurements_by_id = {item["measurement_id"]: item for item in (measurements or [])}
    items = [
        _item_view(
            item,
            decisions_by_candidate.get(item["candidate_id"]),
            measurements_by_id,
            (enrichment or {}).get(item["candidate_id"]),
        )
        for item in delivery["items"]
    ]

    warnings: list[str] = []
    if delivery.get("delivery_language_mode") == "ZH_TRANSLATION" and any(
        item.get("rendered_title") is None for item in delivery["items"]
    ):
        warnings.append("译文尚未生成：显示标题为原文")

    return {
        "brief_id": f"brief-{delivery['delivery_id']}",
        "rendered_at": rendered_at.isoformat(timespec="seconds"),
        "delivery_ref": delivery["delivery_id"],
        "format": "MARKDOWN",
        "language_mode": delivery["delivery_language_mode"],
        "title": f"个人信息简报 · {rendered_at.date().isoformat()}",
        "body_markdown": _markdown(delivery, items, rendered_at),
        "items": items,
        "warnings": warnings,
        "contract_version": "1.0",
    }
