"""交付对象装配（CAP-DEL-01）。

只做装配：不重新筛选、不调整优先级、不补充事实。
呈现（翻译、排版、推送）属于后面的层。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

CHANNEL_ORDER = {"CORE": 0, "EXPLORATION": 1, "VISUAL_MATCH": 2, "VISUAL_EXPLORATION": 3}


def build_delivery(
    delivery_id: str,
    built_at: datetime,
    candidates_by_id: dict[str, dict[str, Any]],
    decisions: list[dict[str, Any]],
    delivery_order: list[str],
    actions: dict[str, str],
    policy_config: dict[str, Any],
) -> dict[str, Any]:
    decisions_by_candidate = {item["candidate_id"]: item for item in decisions}
    language_mode = policy_config["language"]["delivery_language_mode"]
    timestamp = built_at.isoformat(timespec="seconds")

    items: list[dict[str, Any]] = []
    for rank, candidate_id in enumerate(delivery_order, start=1):
        decision = decisions_by_candidate[candidate_id]
        candidate = candidates_by_id[candidate_id]
        caveats: list[str] = []
        if language_mode == "ZH_TRANSLATION":
            caveats.append("译文尚未生成：M1 只交付结构化对象，翻译在呈现层完成")

        items.append(
            {
                "candidate_id": candidate_id,
                "priority_rank": rank,
                "grouping": decision["portfolio"]["channel"],
                "source_url": candidate["canonical_url"],
                "original_title": {
                    "text": candidate["title"]["text"],
                    "language": candidate["title"]["language"],
                },
                "rendered_title": None,
                "delivery_language_mode": language_mode,
                "rationale_refs": [decision["selection_id"]],
                "required_caveats": caveats,
                "recommended_action": actions.get(candidate_id, "SAVE"),
            }
        )

    empty_reason = None
    if not items:
        empty_reason = {"code": "NO_ELIGIBLE_CANDIDATES", "detail_ref": None}

    return {
        "delivery_id": delivery_id,
        "built_at": timestamp,
        "selection_refs": [decisions_by_candidate[cid]["selection_id"] for cid in delivery_order],
        "items": items,
        "delivery_language_mode": language_mode,
        "empty_reason": empty_reason,
        "contract_version": "1.0",
    }
