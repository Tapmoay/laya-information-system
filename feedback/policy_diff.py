"""规则变更的离线对照（CAP-PUP-01）。

在历史输入上重跑两次选择：一次用变更前的策略，一次用变更后的策略。
报告必须同时写出改善与被挤压的部分——只报好消息的对照等于没做。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from feedback.proposal import apply_changes
from selection.engine import select
from tools.validate_objects import validate

DELIVERING_CHANNELS = ("CORE", "EXPLORATION")


def _decision_map(result) -> dict[str, dict[str, Any]]:
    return {item["candidate_id"]: item for item in result.decisions}


def _channel_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        if decision["decision"] != "DELIVER":
            continue
        channel = decision["portfolio"]["channel"]
        counts[channel] = counts.get(channel, 0) + 1
    return counts


def evaluate_diff(
    *,
    report_id: str,
    candidates: list[dict[str, Any]],
    measurements: list[dict[str, Any]],
    demand: dict[str, Any],
    policy_config: dict[str, Any],
    baseline: dict[str, Any],
    candidate_policy: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
    evaluated_at: datetime,
    proposal_ref: str | None = None,
) -> dict[str, Any]:
    if candidate_policy is None:
        if not changes:
            raise ValueError("必须给出 candidate_policy 或 changes")
        candidate_policy = apply_changes(baseline, changes)

    before = select(candidates, measurements, demand, policy_config, policy=baseline, decided_at=evaluated_at)
    after = select(candidates, measurements, demand, policy_config, policy=candidate_policy, decided_at=evaluated_at)

    before_map = _decision_map(before)
    after_map = _decision_map(after)

    affected: list[dict[str, Any]] = []
    improvements: list[str] = []
    squeezes: list[str] = []

    for candidate_id in sorted(before_map):
        old = before_map[candidate_id]
        new = after_map[candidate_id]
        if old["decision"] == new["decision"] and old["portfolio"]["channel"] == new["portfolio"]["channel"]:
            continue
        affected.append(
            {
                "candidate_id": candidate_id,
                "before": old["decision"],
                "after": new["decision"],
                "from_channel": old["portfolio"]["channel"],
                "to_channel": new["portfolio"]["channel"],
            }
        )
        if new["decision"] == "DELIVER" and old["decision"] != "DELIVER":
            improvements.append(f"{candidate_id}: {old['decision']} → DELIVER")
        elif old["decision"] == "DELIVER" and new["decision"] != "DELIVER":
            squeezes.append(f"{candidate_id}: DELIVER → {new['decision']}")

    before_counts = _channel_counts(before.decisions)
    after_counts = _channel_counts(after.decisions)
    channel_deltas = []
    for channel in sorted(set(before_counts) | set(after_counts)):
        delta = {
            "channel": channel,
            "before": before_counts.get(channel, 0),
            "after": after_counts.get(channel, 0),
        }
        channel_deltas.append(delta)
        if delta["after"] > delta["before"]:
            improvements.append(f"{channel} 交付数 {delta['before']} → {delta['after']}")
        elif delta["after"] < delta["before"]:
            squeezes.append(f"{channel} 交付数 {delta['before']} → {delta['after']}")

    report = {
        "report_id": report_id,
        "proposal_ref": proposal_ref,
        "evaluated_at": evaluated_at.isoformat(timespec="seconds"),
        "baseline_policy_version": baseline["policy_version"],
        "candidate_policy_version": candidate_policy["policy_version"],
        "affected_candidates": affected,
        "channel_deltas": channel_deltas,
        "improvements": improvements,
        "squeezes": squeezes,
        "contract_version": "1.0",
    }
    problems = validate("policy-diff-report", report)
    if problems:
        raise ValueError("对照报告违反契约：" + "; ".join(problems))
    return report
