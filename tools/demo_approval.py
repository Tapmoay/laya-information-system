"""演示规则变更的命令行全集：生成提案 → 对照 → 批准 → 版本 → 回滚。

    python -m tools.demo_approval --out runs/demo-approval
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from feedback import approve_cli, diff_cli
from feedback.policy_store import PolicyStore
from feedback.proposal import build_proposal
from selection.engine import load_policy

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-18T05:20:00+08:00"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规则变更命令行演示")
    parser.add_argument("--out", default="runs/demo-approval")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    proposal = build_proposal(
        load_policy(),
        proposal_id="prop-cli-demo",
        rule_id="RULE-ADJACENT-CHANNEL",
        rationale="邻接内容长期挤占核心通道的交付位",
        trigger_observation="连续三轮核心交付都是邻接相关内容",
        evidence_refs=["runs/2026-10-18/run-r2/run-manifest.json"],
        changes=[{"pointer": "/channels/ADJACENT", "before": "CORE", "after": "EXPLORATION"}],
        expected_effect="核心通道只保留直接相关内容",
        side_effects=["可能让邻接内容失去核心位曝光"],
        review_condition="两周后检查核心通道是否长期为空",
        rollback_condition="核心通道连续五轮无交付",
        created_at=datetime.fromisoformat(NOW),
    )
    proposal_path = out / "proposal.json"
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")

    print("== diff ==")
    if diff_cli.main([
        "--proposal", str(proposal_path),
        "--case", str(ROOT / "fixtures" / "m1" / "basic"),
        "--out", str(out / "diff.json"),
        "--at", NOW,
    ]) != 0:
        return 1

    print("== approve (wrong confirm) ==")
    wrong = approve_cli.main([
        "--proposal", str(proposal_path), "--diff", str(out / "diff.json"),
        "--store", str(out / "versions"), "--approver", "用户",
        "--confirm", "我不看", "--at", NOW,
    ])
    print(f"exit={wrong}（预期 2）")

    print("== approve ==")
    code = approve_cli.main([
        "--proposal", str(proposal_path), "--diff", str(out / "diff.json"),
        "--store", str(out / "versions"), "--approver", "用户",
        "--confirm", "RULE-ADJACENT-CHANNEL", "--note", "看过挤压了", "--at", NOW,
    ])
    if code != 0:
        return code

    print("== versions ==")
    print(PolicyStore(out / "versions").versions())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
