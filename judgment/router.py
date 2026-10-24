"""判断结果的路由：模型给概率，代码决定"直接执行"还是"交给 Agent"。

这是用户明确要求的架构：
- **没有分歧**（置信度与领先幅度都够）→ 代码按标签直接执行，不惊动大模型；
- **有分歧**（置信度低或前两名咬得紧）→ 升级给 Agent 处理，模型不下结论。

路由是确定性的：同样的概率分布永远得到同样的结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

AUTO = "AUTO"
ESCALATE = "ESCALATE"

DEFAULT_ACCEPT_THRESHOLD = 0.75
DEFAULT_MARGIN_THRESHOLD = 0.25


@dataclass(frozen=True)
class RoutingDecision:
    route: str
    label: str | None
    confidence: float
    margin: float
    reason: str
    probabilities: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
            "reason": self.reason,
            "probabilities": {key: round(value, 4) for key, value in self.probabilities.items()},
        }


def route(
    probabilities: dict[str, float] | None,
    *,
    accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
) -> RoutingDecision:
    """按概率决定直接执行还是升级。

    升级条件（任一满足）：
    1. 没有概率（模型没给，或适配器不支持）；
    2. 概率不构成合法分布（负数、全零、非数字）；
    3. 最高概率低于 accept_threshold；
    4. 前两名差距小于 margin_threshold——"两个都像"就是分歧。
    """
    if not probabilities:
        return RoutingDecision(ESCALATE, None, 0.0, 0.0, "NO_PROBABILITIES")

    cleaned: dict[str, float] = {}
    for label, value in probabilities.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            return RoutingDecision(ESCALATE, None, 0.0, 0.0, "INVALID_PROBABILITIES")
        if number < 0:
            return RoutingDecision(ESCALATE, None, 0.0, 0.0, "INVALID_PROBABILITIES")
        cleaned[label] = number

    total = sum(cleaned.values())
    if total <= 0:
        return RoutingDecision(ESCALATE, None, 0.0, 0.0, "INVALID_PROBABILITIES")
    if abs(total - 1.0) > 0.01:
        cleaned = {label: value / total for label, value in cleaned.items()}

    ranked = sorted(cleaned.items(), key=lambda item: item[1], reverse=True)
    label, confidence = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = confidence - second

    if confidence < accept_threshold:
        return RoutingDecision(ESCALATE, label, confidence, margin, "LOW_CONFIDENCE", cleaned)
    if margin < margin_threshold:
        return RoutingDecision(ESCALATE, label, confidence, margin, "CLOSE_CALL", cleaned)
    return RoutingDecision(AUTO, label, confidence, margin, "CONFIDENT", cleaned)


def apply(
    result: dict[str, Any],
    *,
    accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
) -> tuple[dict[str, Any], RoutingDecision]:
    """把一个 DecisionResult 变成"可直接用"或"已升级"的形态。

    返回（新的結果, 路由结论）。升级时把状态改成 ABSTAIN/ESCALATED_TO_AGENT，
    并把模型给出的首选标签留在 detail 里，方便 Agent 参考——但不当作结论。
    """
    decision = route(
        result.get("probabilities"),
        accept_threshold=accept_threshold,
        margin_threshold=margin_threshold,
    )
    if decision.route == AUTO:
        updated = dict(result)
        updated["label"] = decision.label
        return updated, decision

    updated = dict(result)
    updated["status"] = "ABSTAIN"
    updated["label"] = None
    updated["reason_code"] = "ESCALATED_TO_AGENT"
    return updated, decision
