"""规则激活的人工闸门（命令行版）。

设计目标：让"批准"变成一个需要真正读过对照报告的动作，而不是一句 --yes。

    python -m feedback.approve --proposal prop.json --diff diff.json \
        --policy selection/policy.yaml --store policy/versions \
        --approver "你的名字" --confirm RULE-ADJACENT-CHANNEL --note "看过挤压了"

--confirm 必须逐字等于提案里的 rule_id；写错就拒绝。这不是防呆，是防"顺手点一下"。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from feedback.activation import ActivationError, Approval, activate
from feedback.policy_store import PolicyStore, RecordStore
from selection.engine import load_policy
from tools.validate_objects import validate


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="批准并激活一条规则变更")
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--diff", required=True)
    parser.add_argument("--policy", default=None, help="基线策略 YAML；缺省用仓库默认")
    parser.add_argument("--store", required=True, help="版本目录")
    parser.add_argument("--records", default=None, help="激活记录目录；缺省为 store 的同级 records/")
    parser.add_argument("--approver", required=True)
    parser.add_argument("--confirm", required=True, help="逐字输入 proposal 里的 rule_id")
    parser.add_argument("--note", default="")
    parser.add_argument("--at", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
    report = json.loads(Path(args.diff).read_text(encoding="utf-8"))
    policy = load_policy(args.policy)

    for kind, payload, label in (
        ("policy-proposal", proposal, "提案"),
        ("policy-diff-report", report, "对照报告"),
    ):
        problems = validate(kind, payload)
        if problems:
            print(f"{label}违反契约：" + "; ".join(problems))
            return 2

    print(f"提案   {proposal['proposal_id']}  rule={proposal['rule_id']}  status={proposal['status']}")
    print(f"理由   {proposal['rationale']}")
    for change in proposal["changes"]:
        print(f"变更   {change['pointer']}: {change['before']!r} -> {change['after']!r}")
    print(f"对照   受影响 {len(report['affected_candidates'])} 条，挤压 {len(report['squeezes'])} 项，改善 {len(report['improvements'])} 项")
    for item in report["squeezes"]:
        print(f"        squeeze: {item}")
    print(f"副作用 {proposal['side_effects']}")
    print(f"回滚   {proposal['rollback_condition']}")

    if args.confirm != proposal["rule_id"]:
        print(f"确认失败：--confirm 必须逐字等于 {proposal['rule_id']}")
        return 2

    at = datetime.fromisoformat(args.at) if args.at else datetime.now().astimezone()
    store = PolicyStore(args.store)
    records = RecordStore(args.records or str(Path(args.store).parent / "records"))

    if args.dry_run:
        print("dry-run：未写入任何版本或记录")
        return 0

    try:
        outcome = activate(
            activation_id=f"act-{proposal['proposal_id']}",
            proposal=proposal,
            diff_report=report,
            approval=Approval(approved_by=args.approver, approved_at=at, note=args.note),
            policy=policy,
            store=store,
            at=at,
        )
    except (ActivationError, ValueError) as exc:
        print(f"激活失败：{exc}")
        return 2

    records.append("activations", outcome["record"])
    print(f"已激活 {outcome['record']['from_policy_version']} -> {outcome['record']['to_policy_version']}")
    print(f"版本   {store.versions()}")
    print(f"回滚   读回 {outcome['record']['rollback_to']}")
    return 0
