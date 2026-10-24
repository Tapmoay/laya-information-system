"""确定性选择器（CAP-SEL-01..05）。

三条不可违反的规则，对应 tests/test_selection.py 的回归测试：

1. **不调用模型。** 本模块不导入 torch / transformers / requests 等；选择只读测量结果。
2. **不用单一总分。** 各分量分别计算并保留来源；排序是逐项词典序，
   任何一个维度的差距都不能被其他维度的高分补偿掉。
3. **未知不是负面证据。** RELEVANCE 为 ABSTAIN/UNKNOWN 的候选进入 ABSTAIN，
   而不是被当成 UNRELATED 丢掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).with_name("policy.yaml")

COMPONENT_OF_TYPE = {
    "RELEVANCE": "TASK",
    "GROWTH_VALUE": "CAPABILITY",
    "DECISION_IMPACT": "DECISION",
    "EXPLORATION_VALUE": "EXPLORATION",
}

NEGATIVE_INFINITY = float("-inf")


@dataclass(frozen=True)
class MeasurementView:
    measurement_id: str
    measurement_type: str
    status: str
    label: str | None
    reason_code: str | None


@dataclass
class Evaluation:
    candidate_id: str
    eligibility: str = "ABSTAIN"
    blocked_reasons: list[str] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)
    channel: str = "NONE"
    role: str = "NONE"
    decision: str = "ABSTAIN"
    reasons: list[dict[str, Any]] = field(default_factory=list)
    monitor_only: bool = False
    order_key: tuple[Any, ...] = ()

    def component_value(self, name: str) -> float | None:
        for component in self.components:
            if component["name"] == name:
                return component["value"]
        return None


@dataclass
class SelectionResult:
    decisions: list[dict[str, Any]]
    delivery_order: list[str]
    constraint_checks: list[dict[str, Any]]
    actions: dict[str, str] = field(default_factory=dict)


def load_policy(path: Path | str | None = None) -> dict[str, Any]:
    """读取选择策略。

    显式路径 > 环境变量 LAYA_POLICY_PATH > 仓库默认文件。
    这样"激活的新版本"和"回滚后的旧版本"都能被同一套代码加载。
    """
    import os

    if path is None:
        path = os.environ.get("LAYA_POLICY_PATH") or POLICY_PATH
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _is_s_level(views: dict[str, MeasurementView]) -> bool:
    """只有 IMPORTANCE=S 且测量状态为 OK 才算 S 级。

    未知不算 S：拿不准的东西不该拥有打破容量限制的权力。
    """
    view = views.get("IMPORTANCE")
    return bool(view and view.status == "OK" and view.label == "S")


def index_measurements(
    measurements: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, MeasurementView]], dict[str, list[str]]]:
    """按 candidate_id + measurement_type 建索引。

    同一候选同一类型出现多条时，取 measurement_id 最小的一条，并记录
    degraded 标记——重复测量本身就是需要暴露的问题，不能静默取第一条。
    """
    grouped: dict[str, dict[str, list[MeasurementView]]] = {}
    for item in measurements:
        view = MeasurementView(
            measurement_id=item["measurement_id"],
            measurement_type=item["measurement_type"],
            status=item["state"]["status"],
            label=(item.get("value") or {}).get("label"),
            reason_code=item["state"].get("reason_code"),
        )
        grouped.setdefault(item["candidate_id"], {}).setdefault(view.measurement_type, []).append(view)

    index: dict[str, dict[str, MeasurementView]] = {}
    degraded: dict[str, list[str]] = {}
    for candidate_id, by_type in grouped.items():
        index[candidate_id] = {}
        for measurement_type, views in by_type.items():
            views.sort(key=lambda v: v.measurement_id)
            index[candidate_id][measurement_type] = views[0]
            if len(views) > 1:
                labels = {v.label for v in views}
                if len(labels) > 1 or any(v.status != "OK" for v in views):
                    degraded.setdefault(candidate_id, []).append(measurement_type)
                else:
                    degraded.setdefault(candidate_id, []).append(measurement_type)
    return index, degraded


def _measurement_label(
    views: dict[str, MeasurementView], measurement_type: str
) -> tuple[str | None, MeasurementView | None]:
    view = views.get(measurement_type)
    if view is None or view.status != "OK" or view.label is None:
        return None, view
    return view.label, view


def _excluded_topic_hit(candidate: dict[str, Any], excluded: list[str]) -> str | None:
    haystack = " ".join(
        [candidate["title"]["text"]]
        + [claim["text"] for claim in candidate.get("raw_claims", [])]
    ).casefold()
    for topic in excluded:
        needle = topic.casefold()
        if needle and needle in haystack:
            return topic
    return None


def _build_components(
    views: dict[str, MeasurementView], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for measurement_type, component_name in COMPONENT_OF_TYPE.items():
        label, view = _measurement_label(views, measurement_type)
        if label is None or view is None:
            continue
        value = policy["label_values"][measurement_type].get(label)
        if value is None:
            continue
        components.append(
            {
                "name": component_name,
                "value": float(value),
                "source_measurement_refs": [view.measurement_id],
            }
        )
    return components


def _reason(code: str, detail_ref: str | None = None) -> dict[str, Any]:
    return {"code": code, "detail_ref": detail_ref}


def evaluate_candidate(
    candidate: dict[str, Any],
    views: dict[str, MeasurementView],
    demand: dict[str, Any],
    policy: dict[str, Any],
) -> Evaluation:
    evaluation = Evaluation(candidate_id=candidate["candidate_id"])

    excluded = demand.get("constraints", {}).get("excluded_topics", []) or []
    hit = _excluded_topic_hit(candidate, excluded)
    if hit:
        evaluation.eligibility = "INELIGIBLE"
        evaluation.blocked_reasons.append(f"命中排除主题：{hit}")
        evaluation.decision = "DISCARD"
        evaluation.reasons = [_reason("POLICY_EXCLUDED")]
        return evaluation

    relevance, relevance_view = _measurement_label(views, "RELEVANCE")
    if relevance is None:
        if relevance_view is None:
            evaluation.reasons = [_reason("MEASUREMENT_MISSING")]
        elif relevance_view.status in {"ABSTAIN", "UNKNOWN", "PARTIAL"}:
            evaluation.reasons = [_reason("EVIDENCE_INSUFFICIENT")]
        else:
            evaluation.reasons = [_reason("MEASUREMENT_DEGRADED")]
        evaluation.eligibility = "ABSTAIN"
        evaluation.decision = "ABSTAIN"
        return evaluation

    if relevance == "UNRELATED":
        evaluation.eligibility = "INELIGIBLE"
        evaluation.blocked_reasons.append("与本轮需求无关")
        evaluation.decision = "DISCARD"
        evaluation.reasons = [_reason("NO_DEMAND_MATCH", relevance_view.measurement_id)]
        return evaluation

    if relevance == "INSUFFICIENT_EVIDENCE":
        evaluation.eligibility = "ABSTAIN"
        evaluation.decision = "ABSTAIN"
        evaluation.reasons = [_reason("EVIDENCE_INSUFFICIENT", relevance_view.measurement_id)]
        return evaluation

    channel = policy["channels"].get(relevance)
    if channel is None:
        evaluation.eligibility = "ABSTAIN"
        evaluation.decision = "ABSTAIN"
        evaluation.reasons = [_reason("MEASUREMENT_DEGRADED", relevance_view.measurement_id)]
        return evaluation

    if channel == "EXPLORATION" and not demand.get("exploration_intent", {}).get("enabled", False):
        evaluation.eligibility = "INELIGIBLE"
        evaluation.blocked_reasons.append("本轮未开启探索通道")
        evaluation.decision = "DISCARD"
        evaluation.reasons = [_reason("NO_DEMAND_MATCH", relevance_view.measurement_id)]
        return evaluation

    evaluation.eligibility = "ELIGIBLE"
    evaluation.channel = channel
    evaluation.components = _build_components(views, policy)
    evaluation.reasons = [_reason("DEMAND_MATCH", relevance_view.measurement_id)]

    maturity, _ = _measurement_label(views, "MATURITY")
    claims, _ = _measurement_label(views, "CLAIM_EVIDENCE")
    if maturity in policy["monitor_rules"]["maturity_states"] or claims in policy["monitor_rules"]["claim_relations"]:
        evaluation.monitor_only = True
        evaluation.decision = "MONITOR"
        evaluation.role = "MONITOR"
        evaluation.reasons.append(_reason("AWAITING_EVIDENCE"))
    return evaluation


def _recommended_action(views: dict[str, MeasurementView], policy: dict[str, Any]) -> str:
    """把测量标签映射为交付动作（APPLY/STUDY/VERIFY/...）。规则来自 policy.yaml，避免散落在代码里。"""
    for rule in policy.get("action_rules", []):
        condition = rule.get("when") or {}
        matched = True
        for key, expected in condition.items():
            if key == "decision_impact":
                label, _ = _measurement_label(views, "DECISION_IMPACT")
            elif key == "claim_evidence":
                label, _ = _measurement_label(views, "CLAIM_EVIDENCE")
            elif key == "maturity":
                label, _ = _measurement_label(views, "MATURITY")
            elif key == "growth_value":
                label, _ = _measurement_label(views, "GROWTH_VALUE")
            elif key == "relevance":
                label, _ = _measurement_label(views, "RELEVANCE")
            else:
                matched = False
                break
            allowed = expected if isinstance(expected, list) else [expected]
            if label not in allowed:
                matched = False
                break
        if matched:
            return rule["action"]
    return "SAVE"


def _order_key(
    evaluation: Evaluation, views: dict[str, MeasurementView], policy: dict[str, Any], channel: str
) -> tuple[Any, ...]:
    key: list[Any] = []
    for criterion in policy["ordering"][channel]:
        if criterion == "recency":
            label, _ = _measurement_label(views, "RECENCY")
            value = policy["label_values"]["RECENCY"].get(label, -1) if label else -1
        else:
            value = evaluation.component_value(criterion.upper())
            value = NEGATIVE_INFINITY if value is None else value
        key.append(-value if isinstance(value, (int, float)) else value)
    key.append(evaluation.candidate_id)
    return tuple(key)


def select(
    candidates: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    demand: dict[str, Any],
    policy_config: dict[str, Any],
    policy: dict[str, Any] | None = None,
    decided_at: datetime | None = None,
) -> SelectionResult:
    """对一批候选执行准入、排序、组合与容量检查，产出结构化决定。"""
    policy = policy or load_policy()
    decided_at = decided_at or datetime.now().astimezone()
    timestamp = decided_at.isoformat(timespec="seconds")

    index, degraded = index_measurements(measurements)
    evaluations: list[Evaluation] = []
    views_by_candidate: dict[str, dict[str, MeasurementView]] = {}

    for candidate in sorted(candidates, key=lambda c: c["candidate_id"]):
        views = index.get(candidate["candidate_id"], {})
        views_by_candidate[candidate["candidate_id"]] = views
        evaluation = evaluate_candidate(candidate, views, demand, policy)
        if candidate["candidate_id"] in degraded:
            evaluation.reasons.append(_reason("MEASUREMENT_DEGRADED"))
        evaluations.append(evaluation)

    capacity = policy_config["capacity"]
    taken: dict[str, list[Evaluation]] = {"CORE": [], "EXPLORATION": []}
    rejected: list[Evaluation] = []
    breakout: set[str] = set()

    for channel in ("CORE", "EXPLORATION"):
        field_name = policy["capacity_fields"][channel]
        limit = int(capacity[field_name])
        pool = [
            e for e in evaluations
            if e.eligibility == "ELIGIBLE" and e.channel == channel and not e.monitor_only
        ]
        pool.sort(key=lambda e: _order_key(e, views_by_candidate[e.candidate_id], policy, channel))
        taken[channel] = pool[:limit]
        overflow = pool[limit:]

        # 政策 §3.8：S 级重大变化可单独提示，但必须解释为什么打破容量。
        if capacity.get("s_breakout_allowed"):
            for evaluation in list(overflow):
                if _is_s_level(views_by_candidate[evaluation.candidate_id]):
                    taken[channel].append(evaluation)
                    overflow.remove(evaluation)
                    breakout.add(evaluation.candidate_id)

        rejected.extend(overflow)

    selected_ids = {e.candidate_id for items in taken.values() for e in items}
    for evaluation in evaluations:
        if evaluation.eligibility != "ELIGIBLE":
            continue
        if evaluation.candidate_id in selected_ids:
            evaluation.decision = "DELIVER"
            evaluation.role = "PRIMARY" if taken[evaluation.channel][0] is evaluation else "SUPPORTING"
            if evaluation.candidate_id in breakout:
                # 容量检查会如实显示 actual > limit，这里补上"为什么允许"
                evaluation.reasons.append(_reason("S_LEVEL_BREAKOUT"))
        elif evaluation.monitor_only:
            evaluation.decision = "MONITOR"
            evaluation.role = "MONITOR"
        else:
            evaluation.decision = "MONITOR"
            evaluation.role = "MONITOR"
            evaluation.reasons.append(_reason("CAPACITY_LIMIT"))

    checks: list[dict[str, Any]] = []
    for channel in ("CORE", "EXPLORATION"):
        field_name = policy["capacity_fields"][channel]
        limit = int(capacity[field_name])
        checks.append(
            {
                "constraint_id": f"{channel}_CAPACITY",
                "limit": limit,
                "actual": len(taken[channel]),
                "passed": len(taken[channel]) <= limit,
            }
        )

    decisions: list[dict[str, Any]] = []
    for evaluation in evaluations:
        decisions.append(
            {
                "selection_id": f"sel-{evaluation.candidate_id}",
                "candidate_id": evaluation.candidate_id,
                "decided_at": timestamp,
                "eligibility": {
                    "decision": evaluation.eligibility,
                    **({"blocked_reasons": evaluation.blocked_reasons} if evaluation.blocked_reasons else {}),
                },
                "priority_components": evaluation.components,
                "portfolio": {"channel": evaluation.channel, "role": evaluation.role},
                "decision": evaluation.decision,
                "reasons": evaluation.reasons or [_reason("MEASUREMENT_MISSING")],
                "constraint_checks": [
                    check for check in checks
                    if check["constraint_id"].startswith(evaluation.channel)
                ] if evaluation.channel != "NONE" else [],
                "policy_version": policy["policy_version"],
                "selector_version": policy["selector_version"],
                "contract_version": "1.0",
            }
        )

    delivery_order = [
        e.candidate_id
        for channel in ("CORE", "EXPLORATION")
        for e in taken[channel]
    ]
    actions = {
        e.candidate_id: _recommended_action(views_by_candidate[e.candidate_id], policy)
        for e in evaluations
        if e.eligibility == "ELIGIBLE"
    }
    return SelectionResult(
        decisions=decisions,
        delivery_order=delivery_order,
        constraint_checks=checks,
        actions=actions,
    )
