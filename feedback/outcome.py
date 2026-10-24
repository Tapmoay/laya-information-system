"""结果关联（CAP-FBK-02）。

核心规则只有一条：**没有观察到结果，就不下收益结论**。
点开、收藏、停留时长都不是收益；它们最多是"发生过什么"。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tools.validate_objects import validate


class OutcomeError(ValueError):
    pass


def build_outcome(
    *,
    outcome_id: str,
    candidate_id: str,
    recorded_at: datetime,
    feedback_records: list[dict[str, Any]] | None = None,
    actual_use: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    benefit_judgement: str = "NOT_EVALUATED",
) -> dict[str, Any]:
    feedback_records = feedback_records or []
    if observed is None:
        observed = {"status": "UNKNOWN", "statement": None, "evidence_refs": []}
    observed.setdefault("evidence_refs", [])
    observed.setdefault("statement", None)

    if observed["status"] == "OBSERVED" and not observed["evidence_refs"]:
        raise OutcomeError("声称已观察结果，但没有给出任何证据引用")

    if benefit_judgement in {"SUPPORTED", "NOT_SUPPORTED"} and observed["status"] != "OBSERVED":
        raise OutcomeError("没有观察到结果时，不能判定收益被支持或不支持")

    record = {
        "outcome_id": outcome_id,
        "candidate_id": candidate_id,
        "feedback_refs": [item["feedback_id"] for item in feedback_records],
        "actual_use": actual_use,
        "observed_outcome": observed,
        "benefit_judgement": benefit_judgement,
        "recorded_at": recorded_at.isoformat(timespec="seconds"),
        "contract_version": "1.0",
    }
    problems = validate("outcome-record", record)
    if problems:
        raise OutcomeError("结果记录违反契约：" + "; ".join(problems))
    return record


def summarize_actions(feedback_records: list[dict[str, Any]]) -> dict[str, int]:
    """只做计数。不把计数翻译成"有用"——那是另一件事，需要证据。"""
    counts: dict[str, int] = {}
    for record in feedback_records:
        if record.get("kind") != "OBSERVED_ACTION":
            continue
        action = (record.get("observed_action") or {}).get("action")
        if action:
            counts[action] = counts.get(action, 0) + 1
    return counts
