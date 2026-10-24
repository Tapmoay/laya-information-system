"""反馈命令行（CAP-FBK-01/02 的可操作入口）。

    python -m feedback.cli user-stated --store runs/feedback --id fb-1 \
        --candidate cand-001 --text "这个方向想深入"
    python -m feedback.cli observed --store runs/feedback --id fb-2 \
        --candidate cand-001 --action OPENED
    python -m feedback.cli outcome --store runs/feedback --id out-1 \
        --candidate cand-001 --use APPLIED --observed-status OBSERVED \
        --evidence runs/2026-10-18/run-r2/run-manifest.json
    python -m feedback.cli list --store runs/feedback

三类反馈仍然在结构上互斥：命令行本身就没有"把用户说的话塞进行为记录"这种选项。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from feedback.capture import record_agent_inference, record_observed_action, record_user_stated
from feedback.outcome import build_outcome
from feedback.policy_store import RecordStore


def _at(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else datetime.now().astimezone()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="feedback", description="记录反馈、结果，并查看存储")
    sub = parser.add_subparsers(dest="command", required=True)

    stated = sub.add_parser("user-stated", help="用户明确说过的话")
    stated.add_argument("--store", required=True)
    stated.add_argument("--id", required=True)
    stated.add_argument("--candidate", required=True)
    stated.add_argument("--text", required=True)
    stated.add_argument("--sentiment", default=None)
    stated.add_argument("--delivery", default=None)
    stated.add_argument("--at", default=None)

    observed = sub.add_parser("observed", help="可观察行为（不是收益）")
    observed.add_argument("--store", required=True)
    observed.add_argument("--id", required=True)
    observed.add_argument("--candidate", required=True)
    observed.add_argument("--action", required=True, choices=["OPENED", "DISMISSED", "SAVED", "SKIPPED", "READ_TIME"])
    observed.add_argument("--value", type=float, default=None)
    observed.add_argument("--delivery", default=None)
    observed.add_argument("--at", default=None)

    inferred = sub.add_parser("agent-inferred", help="Agent 的推断（必须带置信度）")
    inferred.add_argument("--store", required=True)
    inferred.add_argument("--id", required=True)
    inferred.add_argument("--candidate", required=True)
    inferred.add_argument("--inference", required=True)
    inferred.add_argument("--confidence", type=float, required=True)
    inferred.add_argument("--evidence", action="append", default=[])
    inferred.add_argument("--at", default=None)

    outcome = sub.add_parser("outcome", help="记录实际使用与观察结果")
    outcome.add_argument("--store", required=True)
    outcome.add_argument("--id", required=True)
    outcome.add_argument("--candidate", required=True)
    outcome.add_argument("--use", default=None, choices=["APPLIED", "STUDIED", "VERIFIED", "SAVED", "NOT_USED"])
    outcome.add_argument("--observed-status", default="UNKNOWN", choices=["OBSERVED", "PARTIALLY_OBSERVED", "NOT_OBSERVED", "UNKNOWN"])
    outcome.add_argument("--statement", default=None)
    outcome.add_argument("--evidence", action="append", default=[])
    outcome.add_argument("--benefit", default="NOT_EVALUATED", choices=["SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "INDETERMINATE", "NOT_EVALUATED"])
    outcome.add_argument("--at", default=None)

    listing = sub.add_parser("list", help="查看已记录的条目")
    listing.add_argument("--store", required=True)
    listing.add_argument("--kind", default="feedback", choices=list(RecordStore.KINDS))

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = build_parser().parse_args(argv)
    store = RecordStore(args.store)

    if args.command == "list":
        records = store.read(args.kind)
        print(f"{args.kind}: {len(records)} 条")
        for record in records:
            print(f"  {record.get('_id')}  {record.get('kind', record.get('benefit_judgement', ''))}")
        return 0

    try:
        if args.command == "user-stated":
            record = record_user_stated(
                feedback_id=args.id,
                candidate_id=args.candidate,
                statement=args.text,
                captured_at=_at(args.at),
                sentiment=args.sentiment,
                delivery_id=args.delivery,
            )
            store.append("feedback", record)
        elif args.command == "observed":
            record = record_observed_action(
                feedback_id=args.id,
                candidate_id=args.candidate,
                action=args.action,
                captured_at=_at(args.at),
                value=args.value,
                delivery_id=args.delivery,
            )
            store.append("feedback", record)
        elif args.command == "agent-inferred":
            record = record_agent_inference(
                feedback_id=args.id,
                candidate_id=args.candidate,
                inference=args.inference,
                confidence=args.confidence,
                captured_at=_at(args.at),
                evidence_refs=args.evidence,
            )
            store.append("feedback", record)
        else:
            related = [
                item
                for item in store.read("feedback")
                if item.get("candidate_id") == args.candidate
            ]
            record = build_outcome(
                outcome_id=args.id,
                candidate_id=args.candidate,
                recorded_at=_at(args.at),
                feedback_records=related,
                actual_use={"kind": args.use} if args.use else None,
                observed={
                    "status": args.observed_status,
                    "statement": args.statement,
                    "evidence_refs": args.evidence,
                },
                benefit_judgement=args.benefit,
            )
            store.append("outcomes", record)
    except ValueError as exc:
        print(f"记录失败：{exc}")
        return 2

    print(f"已记录 {args.id}（{args.command}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
