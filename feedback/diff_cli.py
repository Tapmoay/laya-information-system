"""生成规则变更的对照报告（CAP-PUP-01 的命令行入口）。

    python -m feedback.diff --proposal prop.json --case fixtures/m1/basic \
        --policy selection/policy.yaml --out runs/diff.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from feedback.policy_diff import evaluate_diff
from runtime.offline_run import load_case
from selection.engine import load_policy
from tools.validate_objects import validate


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="规则变更对照")
    parser.add_argument("--proposal", required=True, help="PolicyProposal JSON")
    parser.add_argument("--case", required=True, help="夹具目录（历史输入）")
    parser.add_argument("--policy", default=None, help="基线策略 YAML；缺省用仓库默认")
    parser.add_argument("--policy-config", default=None, help="PolicyConfig JSON；缺省读 case 里的")
    parser.add_argument("--out", default=None, help="把报告写到这个文件")
    parser.add_argument("--report-id", default="diff-cli-0001")
    parser.add_argument("--at", default=None)
    args = parser.parse_args(argv)

    proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
    problems = validate("policy-proposal", proposal)
    if problems:
        print("提案违反契约：" + "; ".join(problems))
        return 2

    case = load_case(args.case)
    baseline = load_policy(args.policy)
    policy_config = (
        json.loads(Path(args.policy_config).read_text(encoding="utf-8"))
        if args.policy_config
        else case.config
    )

    report = evaluate_diff(
        report_id=args.report_id,
        candidates=case.candidates,
        measurements=case.measurements,
        demand=case.demand,
        policy_config=policy_config,
        baseline=baseline,
        changes=proposal["changes"],
        evaluated_at=datetime.fromisoformat(args.at) if args.at else datetime.now().astimezone(),
        proposal_ref=proposal["proposal_id"],
    )

    print(f"affected={len(report['affected_candidates'])} improvements={len(report['improvements'])} squeezes={len(report['squeezes'])}")
    for item in report["affected_candidates"]:
        channel_note = ""
        if item.get("from_channel") != item.get("to_channel"):
            channel_note = f"  [{item.get('from_channel')} -> {item.get('to_channel')}]"
        print(f"  {item['candidate_id']}: {item['before']} -> {item['after']}{channel_note}")
    for item in report["squeezes"]:
        print(f"  squeeze: {item}")
    for item in report["improvements"]:
        print(f"  better : {item}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0
