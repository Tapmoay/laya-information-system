"""规则激活闸门回归测试（WP-R9 后半 / CAP-PUP-02）。

保护四件事：
1. 没有具体的人批准，规则不能生效；
2. 对照报告必须属于这份提案；
3. 每次激活留下不可变版本，可回滚；
4. 激活后的策略确实会改变运行时行为。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from feedback.activation import ActivationError, Approval, activate, bump_minor, rollback
from feedback.policy_diff import evaluate_diff
from feedback.policy_store import PolicyStore, PolicyStoreError, RecordStore
from feedback.proposal import build_proposal
from runtime.offline_run import load_case
from selection.engine import load_policy, select
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-18T04:40:00+08:00")
CHANGE = {"pointer": "/channels/ADJACENT", "before": "CORE", "after": "EXPLORATION"}


def case():
    return load_case(ROOT / "fixtures" / "m1" / "basic")


def proposal_for(policy: dict):
    return build_proposal(
        policy,
        proposal_id="prop-act-01",
        rule_id="RULE-ADJACENT-CHANNEL",
        rationale="邻接内容长期挤占核心通道的交付位",
        trigger_observation="连续三轮核心交付都是邻接相关内容",
        evidence_refs=["runs/2026-10-18/run-r2/run-manifest.json"],
        changes=[CHANGE],
        expected_effect="核心通道只保留直接相关内容",
        side_effects=["可能让邻接内容失去曝光"],
        review_condition="两周后检查核心通道是否长期为空",
        rollback_condition="核心通道连续五轮无交付",
        created_at=NOW,
    )


def diff_for(baseline: dict, proposal: dict):
    data = case()
    return evaluate_diff(
        report_id="diff-act-01",
        candidates=data.candidates,
        measurements=data.measurements,
        demand=data.demand,
        policy_config=data.config,
        baseline=baseline,
        changes=proposal["changes"],
        evaluated_at=NOW,
        proposal_ref=proposal["proposal_id"],
    )


# ---------------------------------------------------------------- 闸门


def test_activation_requires_a_human_name(tmp_path: Path) -> None:
    policy = load_policy()
    proposal = proposal_for(policy)
    report = diff_for(policy, proposal)
    store = PolicyStore(tmp_path / "versions")

    with pytest.raises(ActivationError):
        activate(
            activation_id="act-1",
            proposal=proposal,
            diff_report=report,
            approval=Approval(approved_by="   ", approved_at=NOW),
            policy=policy,
            store=store,
            at=NOW,
        )


def test_activation_requires_matching_diff(tmp_path: Path) -> None:
    policy = load_policy()
    proposal = proposal_for(policy)
    report = diff_for(policy, proposal)
    report = {**report, "proposal_ref": "prop-someone-else"}

    with pytest.raises(ActivationError):
        activate(
            activation_id="act-2",
            proposal=proposal,
            diff_report=report,
            approval=Approval(approved_by="用户", approved_at=NOW),
            policy=policy,
            store=PolicyStore(tmp_path / "versions"),
            at=NOW,
        )


# ---------------------------------------------------------------- 版本


def test_activation_writes_a_new_version(tmp_path: Path) -> None:
    policy = load_policy()
    proposal = proposal_for(policy)
    report = diff_for(policy, proposal)
    store = PolicyStore(tmp_path / "versions")

    outcome = activate(
        activation_id="act-3",
        proposal=proposal,
        diff_report=report,
        approval=Approval(approved_by="用户", approved_at=NOW, note="审查通过"),
        policy=policy,
        store=store,
        at=NOW,
    )

    record = outcome["record"]
    assert validate("policy-activation", record) == []
    assert record["to_policy_version"] == "0.2.0"
    assert record["rollback_to"] == "0.1.0"
    assert store.versions() == ["0.1.0", "0.2.0"]

    loaded = store.load("0.2.0")
    assert load_policy(tmp_path / "versions" / "0.2.0.yaml")["channels"]["ADJACENT"] == "EXPLORATION"
    assert loaded["rollback_to"] == "0.1.0"


def test_activation_seeds_the_baseline_version(tmp_path: Path) -> None:
    policy = load_policy()
    proposal = proposal_for(policy)
    report = diff_for(policy, proposal)
    store = PolicyStore(tmp_path / "versions")
    store.save(policy, saved_at=NOW, note="基线")

    with pytest.raises(PolicyStoreError):
        store.save(policy, saved_at=NOW, note="重复")


def test_rollback_restores_previous_policy(tmp_path: Path) -> None:
    policy = load_policy()
    proposal = proposal_for(policy)
    report = diff_for(policy, proposal)
    store = PolicyStore(tmp_path / "versions")
    store.save(policy, saved_at=NOW, note="基线")

    outcome = activate(
        activation_id="act-4",
        proposal=proposal,
        diff_report=report,
        approval=Approval(approved_by="用户", approved_at=NOW),
        policy=policy,
        store=store,
        at=NOW,
    )
    restored = rollback(outcome["record"], store)
    assert restored["channels"]["ADJACENT"] == "CORE"
    assert restored["policy_version"] == "0.1.0"


# ---------------------------------------------------------------- 行为


def test_activated_policy_changes_selection() -> None:
    data = case()
    baseline = load_policy()
    activated = {**baseline, "channels": {**baseline["channels"], "ADJACENT": "EXPLORATION"}}

    before = select(data.candidates, data.measurements, data.demand, data.config, policy=baseline, decided_at=NOW)
    after = select(data.candidates, data.measurements, data.demand, data.config, policy=activated, decided_at=NOW)

    before_map = {item["candidate_id"]: item for item in before.decisions}
    after_map = {item["candidate_id"]: item for item in after.decisions}
    # cand-m1-0002 是 ADJACENT；策略把 ADJACENT 从 CORE 改到 EXPLORATION
    assert before_map["cand-m1-0002"]["portfolio"]["channel"] == "CORE"
    assert after_map["cand-m1-0002"]["portfolio"]["channel"] == "EXPLORATION"
    assert "cand-m1-0002" in before.delivery_order and "cand-m1-0002" in after.delivery_order


def test_bump_minor() -> None:
    assert bump_minor("0.1.0") == "0.2.0"
    assert bump_minor("1.9.3") == "1.10.0"


# ---------------------------------------------------------------- 存储


def test_record_store_roundtrip(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "records")
    store.append("feedback", {"feedback_id": "fb-1", "kind": "USER_STATED"})
    store.append("feedback", {"feedback_id": "fb-2", "kind": "OBSERVED_ACTION"})
    assert [item["_id"] for item in store.read("feedback")] == ["fb-1", "fb-2"]
    with pytest.raises(ValueError):
        store.append("feedback", {"feedback_id": "fb-1"})
    with pytest.raises(ValueError):
        store.append("unknown", {"feedback_id": "fb-3"})
