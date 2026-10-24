"""规则激活闸门（CAP-PUP-02）。

规则变更必须：有对照报告、有人类批准、留下不可变版本、可回滚。
本模块只做"批准后落版"这一步——它不判断该不该改（那是提案与对照的事）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from feedback.policy_store import PolicyStore
from feedback.proposal import apply_changes
from tools.validate_objects import validate


class ActivationError(ValueError):
    pass


@dataclass
class Approval:
    approved_by: str
    approved_at: datetime
    note: str = ""


def bump_minor(version: str) -> str:
    major, minor, _patch = (int(part) for part in version.split("."))
    return f"{major}.{minor + 1}.0"


def activate(
    *,
    activation_id: str,
    proposal: dict[str, Any],
    diff_report: dict[str, Any],
    approval: Approval,
    policy: dict[str, Any],
    store: PolicyStore,
    at: datetime,
) -> dict[str, Any]:
    if not approval.approved_by.strip():
        raise ActivationError("激活必须由具体的人批准，不能由系统自己签")
    if approval.approved_at is None:
        raise ActivationError("缺少批准时间")
    if proposal.get("status") not in {"PROPOSED", "TRIAL"}:
        raise ActivationError(f"提案状态为 {proposal.get('status')}，不能激活")
    if diff_report.get("proposal_ref") != proposal["proposal_id"]:
        raise ActivationError("对照报告不属于这份提案")
    if "squeezes" not in diff_report:
        raise ActivationError("对照报告缺少 squeezes 字段")

    updated = apply_changes(policy, proposal["changes"])
    from_version = str(policy["policy_version"])
    # 基线必须先落版，否则回滚时无版本可读
    if from_version not in store.versions():
        store.save(policy, saved_at=at, note="激活前基线（自动保存）")
    to_version = bump_minor(from_version)
    updated["policy_version"] = to_version
    updated["rollback_to"] = from_version
    store.save(updated, saved_at=at, note=f"{proposal['rule_id']} 激活")

    record = {
        "activation_id": activation_id,
        "proposal_ref": proposal["proposal_id"],
        "diff_report_ref": diff_report["report_id"],
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at.isoformat(timespec="seconds"),
        "activated_at": at.isoformat(timespec="seconds"),
        "from_policy_version": from_version,
        "to_policy_version": to_version,
        "changes_applied": proposal["changes"],
        "rollback_to": from_version,
        "note": approval.note,
        "contract_version": "1.0",
    }
    problems = validate("policy-activation", record)
    if problems:
        raise ActivationError("激活记录违反契约：" + "; ".join(problems))
    return {"policy": updated, "record": record}


def rollback(record: dict[str, Any], store: PolicyStore) -> dict[str, Any]:
    version = record.get("rollback_to")
    if not version:
        raise ActivationError("激活记录里没有 rollback_to")
    return store.load(version)
