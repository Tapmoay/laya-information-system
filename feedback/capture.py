"""反馈采集（CAP-FBK-01）。

三类反馈在结构上互斥：用户明示、可观察行为、Agent 推断。
这不是风格问题——混写之后，"这是用户说的"和"这是系统猜的"就再也分不开了。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tools.validate_objects import validate


class FeedbackError(ValueError):
    pass


def _base(feedback_id: str, candidate_id: str, kind: str, source: str, captured_at: datetime, delivery_id: str | None) -> dict[str, Any]:
    return {
        "feedback_id": feedback_id,
        "candidate_id": candidate_id,
        "delivery_id": delivery_id,
        "kind": kind,
        "source": source,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "user_stated": None,
        "observed_action": None,
        "agent_inference": None,
        "contract_version": "1.0",
    }


def _finish(record: dict[str, Any]) -> dict[str, Any]:
    problems = validate("feedback-record", record)
    if problems:
        raise FeedbackError("反馈记录违反契约：" + "; ".join(problems))
    return record


def record_user_stated(
    *,
    feedback_id: str,
    candidate_id: str,
    statement: str,
    captured_at: datetime,
    sentiment: str | None = None,
    delivery_id: str | None = None,
) -> dict[str, Any]:
    record = _base(feedback_id, candidate_id, "USER_STATED", "USER_MESSAGE", captured_at, delivery_id)
    record["user_stated"] = {"statement": statement, **({"sentiment": sentiment} if sentiment else {})}
    return _finish(record)


def record_observed_action(
    *,
    feedback_id: str,
    candidate_id: str,
    action: str,
    captured_at: datetime,
    value: float | None = None,
    delivery_id: str | None = None,
    source: str = "UI_ACTION",
) -> dict[str, Any]:
    record = _base(feedback_id, candidate_id, "OBSERVED_ACTION", source, captured_at, delivery_id)
    record["observed_action"] = {"action": action, "value": value}
    return _finish(record)


def record_agent_inference(
    *,
    feedback_id: str,
    candidate_id: str,
    inference: str,
    confidence: float,
    captured_at: datetime,
    evidence_refs: list[str] | None = None,
    delivery_id: str | None = None,
) -> dict[str, Any]:
    record = _base(feedback_id, candidate_id, "AGENT_INFERRED", "AGENT", captured_at, delivery_id)
    record["agent_inference"] = {
        "inference": inference,
        "confidence": confidence,
        "evidence_refs": list(evidence_refs or []),
    }
    return _finish(record)
