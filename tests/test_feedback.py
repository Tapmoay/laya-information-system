"""反馈、结果关联与规则对照回归测试（WP-R9 前半 / CAP-FBK-01/02、CAP-PUP-01）。

保护四件事：
1. 三类反馈结构上不能混写；
2. 点击与停留不能变成"学到了"；
3. 提案必须依据当前配置，不能基于过期值；
4. 规则对照必须同时报告被挤压的部分。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from feedback.capture import record_agent_inference, record_observed_action, record_user_stated
from feedback.outcome import OutcomeError, build_outcome, summarize_actions
from feedback.policy_diff import evaluate_diff
from feedback.proposal import ProposalError, apply_changes, build_proposal, resolve_pointer
from runtime.offline_run import load_case
from selection.engine import load_policy
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-18T04:00:00+08:00")


# ---------------------------------------------------------------- 反馈


def test_three_kinds_are_structurally_separated() -> None:
    stated = record_user_stated(
        feedback_id="fb-1", candidate_id="cand-1", statement="这个方向我想深入", captured_at=NOW, sentiment="LIKE"
    )
    action = record_observed_action(feedback_id="fb-2", candidate_id="cand-1", action="OPENED", captured_at=NOW)
    inferred = record_agent_inference(
        feedback_id="fb-3", candidate_id="cand-1", inference="可能偏好系统工程", confidence=0.4, captured_at=NOW
    )

    assert stated["source"] == "USER_MESSAGE" and stated["observed_action"] is None
    assert action["source"] == "UI_ACTION" and action["user_stated"] is None
    assert inferred["source"] == "AGENT" and inferred["user_stated"] is None
    for record in (stated, action, inferred):
        assert validate("feedback-record", record) == []


def test_mixing_statement_with_action_is_invalid() -> None:
    mixed = {
        "feedback_id": "fb-4",
        "candidate_id": "cand-1",
        "kind": "USER_STATED",
        "source": "UI_ACTION",
        "captured_at": NOW.isoformat(timespec="seconds"),
        "user_stated": {"statement": "喜欢"},
        "observed_action": {"action": "OPENED", "value": None},
        "agent_inference": None,
        "contract_version": "1.0",
    }
    assert validate("feedback-record", mixed) != []


def test_agent_inference_needs_confidence() -> None:
    with pytest.raises(Exception):
        record_agent_inference(
            feedback_id="fb-5", candidate_id="cand-1", inference="偏好", confidence=None, captured_at=NOW
        )


# ---------------------------------------------------------------- 结果


def test_clicks_do_not_become_learning() -> None:
    clicks = [
        record_observed_action(feedback_id="fb-10", candidate_id="cand-1", action="OPENED", captured_at=NOW),
        record_observed_action(feedback_id="fb-11", candidate_id="cand-1", action="READ_TIME", value=420, captured_at=NOW),
    ]
    outcome = build_outcome(outcome_id="out-1", candidate_id="cand-1", recorded_at=NOW, feedback_records=clicks)
    assert outcome["observed_outcome"]["status"] == "UNKNOWN"
    assert outcome["benefit_judgement"] == "NOT_EVALUATED"
    assert summarize_actions(clicks) == {"OPENED": 1, "READ_TIME": 1}


def test_observed_outcome_requires_evidence() -> None:
    with pytest.raises(OutcomeError):
        build_outcome(
            outcome_id="out-2",
            candidate_id="cand-1",
            recorded_at=NOW,
            observed={"status": "OBSERVED", "statement": "好像学会了", "evidence_refs": []},
        )


def test_benefit_needs_observation() -> None:
    with pytest.raises(OutcomeError):
        build_outcome(
            outcome_id="out-3",
            candidate_id="cand-1",
            recorded_at=NOW,
            benefit_judgement="SUPPORTED",
        )


def test_observed_and_judged_outcome_is_valid() -> None:
    outcome = build_outcome(
        outcome_id="out-4",
        candidate_id="cand-1",
        recorded_at=NOW,
        actual_use={"kind": "APPLIED", "detail": "把选择器接进了批次脚本"},
        observed={
            "status": "OBSERVED",
            "statement": "离线链路跑通，三个案例全部通过",
            "evidence_refs": ["runs/2026-10-18/run-r2/run-manifest.json"],
        },
        benefit_judgement="SUPPORTED",
    )
    assert validate("outcome-record", outcome) == []


# ---------------------------------------------------------------- 提案


def policy() -> dict:
    return load_policy()


def test_proposal_rejects_stale_before() -> None:
    with pytest.raises(ProposalError):
        build_proposal(
            policy(),
            proposal_id="prop-1",
            rule_id="RULE-STALE-BEFORE",
            rationale="基于过期配置提出的规则变更",
            trigger_observation="看到一个已经不再成立的现象",
            evidence_refs=["runs/x.json"],
            changes=[{"pointer": "/monitor_rules/maturity_states", "before": ["DEMONSTRATED"], "after": ["ANNOUNCED"]}],
            expected_effect="减少噪声",
            side_effects=["可能压掉重要更新"],
            review_condition="两周后复盘",
            rollback_condition="连续五轮无交付",
            created_at=NOW,
        )


def test_proposal_cannot_be_active() -> None:
    proposal = build_proposal(
        policy(),
        proposal_id="prop-2",
        rule_id="RULE-MONITOR-DEMO",
        rationale="演示阶段的更新不应占用核心交付位",
        trigger_observation="连续三轮核心交付里都有演示阶段的产品更新",
        evidence_refs=["runs/x.json"],
        changes=[{"pointer": "/monitor_rules/maturity_states", "before": ["ANNOUNCED"], "after": ["ANNOUNCED", "DEMONSTRATED"]}],
        expected_effect="核心通道只保留已有可用证据的内容",
        side_effects=["可能压掉确实重要的早期更新"],
        review_condition="两周后检查核心通道是否长期为空",
        rollback_condition="核心通道连续五轮无交付",
        created_at=NOW,
    )
    assert validate("policy-proposal", proposal) == []
    with pytest.raises(Exception):
        build_proposal(
            policy(),
            proposal_id="prop-3",
            rule_id="RULE-DIRECT-ACTIVATION",
            rationale="试图绕过人工闸门直接激活规则",
            trigger_observation="观察到某个重复出现的问题需要立即处理",
            evidence_refs=["runs/x.json"],
            changes=[{"pointer": "/monitor_rules/maturity_states", "before": ["ANNOUNCED"], "after": ["DEMONSTRATED"]}],
            expected_effect="减少核心通道噪声",
            side_effects=["可能压掉重要更新"],
            review_condition="两周后复盘效果",
            rollback_condition="出现关键遗漏时回滚",
            created_at=NOW,
            status="ACTIVE",
        )


def test_apply_changes_does_not_mutate_baseline() -> None:
    baseline = policy()
    snapshot = json.dumps(baseline, sort_keys=True)
    updated = apply_changes(
        baseline,
        [{"pointer": "/monitor_rules/maturity_states", "before": ["ANNOUNCED"], "after": ["ANNOUNCED", "DEMONSTRATED"]}],
    )
    assert json.dumps(baseline, sort_keys=True) == snapshot
    assert updated["monitor_rules"]["maturity_states"] == ["ANNOUNCED", "DEMONSTRATED"]
    assert resolve_pointer(updated, "/monitor_rules/maturity_states") == ["ANNOUNCED", "DEMONSTRATED"]


# ---------------------------------------------------------------- 对照


def _case():
    return load_case(ROOT / "fixtures" / "m1" / "basic")


def test_policy_diff_reports_the_squeeze() -> None:
    case = _case()
    report = evaluate_diff(
        report_id="diff-1",
        candidates=case.candidates,
        measurements=case.measurements,
        demand=case.demand,
        policy_config=case.config,
        baseline=policy(),
        changes=[{"pointer": "/monitor_rules/maturity_states", "before": ["ANNOUNCED"], "after": ["ANNOUNCED", "DEMONSTRATED"]}],
        evaluated_at=NOW,
    )

    assert validate("policy-diff-report", report) == []
    assert any(item["candidate_id"] == "cand-m1-0001" and item["after"] == "MONITOR" for item in report["affected_candidates"])
    core = next(delta for delta in report["channel_deltas"] if delta["channel"] == "CORE")
    assert (core["before"], core["after"]) == (2, 1)
    assert report["squeezes"], "通道变少时必须报告挤压"
    assert not report["improvements"]


def test_identical_policy_reports_no_change() -> None:
    case = _case()
    report = evaluate_diff(
        report_id="diff-2",
        candidates=case.candidates,
        measurements=case.measurements,
        demand=case.demand,
        policy_config=case.config,
        baseline=policy(),
        candidate_policy=policy(),
        evaluated_at=NOW,
    )
    assert report["affected_candidates"] == []
    assert report["improvements"] == []
    assert report["squeezes"] == []
    assert all(delta["before"] == delta["after"] for delta in report["channel_deltas"])
