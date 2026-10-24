"""规则变更提案（CAP-FBK-03 的构造部分）。

本模块不决定"该改什么"——那需要 Agent 基于长期观察判断。
它只负责：把一次提议做成结构合法、依据可查、能回滚的对象，并且核对待改字段
当前值确实等于提案里写的 before（防止提案基于过期配置）。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from tools.validate_objects import validate


class ProposalError(ValueError):
    pass


def resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ProposalError(f"不是合法的 JSON Pointer：{pointer}")
    current = document
    for token in pointer.split("/")[1:]:
        if not isinstance(current, dict) or token not in current:
            raise ProposalError(f"策略中不存在字段：{pointer}")
        current = current[token]
    return current


def apply_changes(policy: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    updated = deepcopy(policy)
    for change in changes:
        tokens = change["pointer"].split("/")[1:]
        current = updated
        for token in tokens[:-1]:
            current = current[token]
        current[tokens[-1]] = change["after"]
    return updated


def build_proposal(
    policy: dict[str, Any],
    *,
    proposal_id: str,
    rule_id: str,
    rationale: str,
    trigger_observation: str,
    evidence_refs: list[str],
    changes: list[dict[str, Any]],
    expected_effect: str,
    side_effects: list[str],
    review_condition: str,
    rollback_condition: str,
    created_at: datetime,
    status: str = "PROPOSED",
) -> dict[str, Any]:
    for change in changes:
        current = resolve_pointer(policy, change["pointer"])
        if current != change["before"]:
            raise ProposalError(
                f"{change['pointer']} 当前值是 {current!r}，与提案中的 before={change['before']!r} 不一致"
            )

    record = {
        "proposal_id": proposal_id,
        "rule_id": rule_id,
        "status": status,
        "rationale": rationale,
        "trigger_observation": trigger_observation,
        "evidence_refs": evidence_refs,
        "changes": changes,
        "expected_effect": expected_effect,
        "side_effects": side_effects,
        "review_condition": review_condition,
        "rollback_condition": rollback_condition,
        "created_at": created_at.isoformat(timespec="seconds"),
        "contract_version": "1.0",
    }
    problems = validate("policy-proposal", record)
    if problems:
        raise ProposalError("提案违反契约：" + "; ".join(problems))
    return record
