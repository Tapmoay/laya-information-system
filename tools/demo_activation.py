"""演示规则变更的完整回路：提案 → 对照 → 激活 → 版本 → 回滚。

    python -m tools.demo_activation --out runs/demo-activation
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from feedback.activation import Approval, activate, rollback
from feedback.policy_diff import evaluate_diff
from feedback.policy_store import PolicyStore
from feedback.proposal import build_proposal
from runtime.offline_run import load_case
from selection.engine import load_policy

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-18T04:40:00+08:00")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规则激活回路演示")
    parser.add_argument("--out", default="runs/demo-activation")
    args = parser.parse_args(argv)

    out = Path(args.out)
    data = load_case(ROOT / "fixtures" / "m1" / "basic")
    baseline = load_policy()
    store = PolicyStore(out / "versions")

    proposal = build_proposal(
        baseline,
        proposal_id="prop-demo-01",
        rule_id="RULE-ADJACENT-CHANNEL",
        rationale="邻接内容长期挤占核心通道的交付位",
        trigger_observation="连续三轮核心交付都是邻接相关内容",
        evidence_refs=["runs/2026-10-18/run-r2/run-manifest.json"],
        changes=[{"pointer": "/channels/ADJACENT", "before": "CORE", "after": "EXPLORATION"}],
        expected_effect="核心通道只保留直接相关内容",
        side_effects=["可能让邻接内容失去核心位曝光"],
        review_condition="两周后检查核心通道是否长期为空",
        rollback_condition="核心通道连续五轮无交付",
        created_at=NOW,
    )

    report = evaluate_diff(
        report_id="diff-demo-01",
        candidates=data.candidates,
        measurements=data.measurements,
        demand=data.demand,
        policy_config=data.config,
        baseline=baseline,
        changes=proposal["changes"],
        evaluated_at=NOW,
        proposal_ref=proposal["proposal_id"],
    )

    outcome = activate(
        activation_id="act-demo-01",
        proposal=proposal,
        diff_report=report,
        approval=Approval(approved_by="用户", approved_at=NOW, note="演示用批准"),
        policy=baseline,
        store=store,
        at=NOW,
    )

    print(f"proposal   {proposal['proposal_id']} ({proposal['status']})")
    print(f"diff       affected={len(report['affected_candidates'])} squeezes={len(report['squeezes'])}")
    for item in report["squeezes"]:
        print(f"           squeeze: {item}")
    print(f"activation {outcome['record']['from_policy_version']} -> {outcome['record']['to_policy_version']}")
    print(f"versions   {store.versions()}")
    restored = rollback(outcome["record"], store)
    print(f"rollback   {restored['policy_version']} channels.ADJACENT={restored['channels']['ADJACENT']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
