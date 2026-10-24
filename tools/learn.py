"""把用户反馈落到参数上：一条命令看清"改了什么、为什么改、能不能撤"。

用法：
  python tools/learn.py --user u1 --scores FILE --veto item-abc        # 记一条"不该给我"
  python tools/learn.py --user u1 --scores FILE --promote item-abc     # 记一条"这条有用"
  python tools/learn.py --user u1 --say "太窄了，老是同类"              # 记一条口头反馈
  python tools/learn.py --user u1 --apply                              # 够数才提交，返回新版本
  python tools/learn.py --user u1 --scores runs/live-<日期>/scores.jsonl \
      --from-overrides runs/agent/<日期>/selection.json --apply       # 每日链路：Agent 取舍 → 参数
  python tools/learn.py --user u1 --history                            # 看全部版本
  python tools/learn.py --user u1 --rollback 2                         # 回滚到 v0002
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.adaptive import (  # noqa: E402
    FeedbackEvent,
    append_pending,
    commit,
    load_ledger,
    load_pending,
    propose,
    record_ledger,
    rollback,
)
from judgment.params import ParameterStore  # noqa: E402
from judgment.scoring import summarise  # noqa: E402
from judgment.suggest import classify  # noqa: E402

DEFAULT_BASE = ROOT / "state" / "params"


def _event_id(payload: str) -> str:
    return "fb-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _load_scores(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["item_id"]] = row
    return rows


# 行为不是收益（policy §7）：这里只把"明确的取舍"翻译成标签，
# 停留时长之类需要解释的信号一律不进。
ACTION_TO_LABEL = {
    "SAVED": 1,
    "OPENED": 1,
    "DISMISSED": 0,
    "SKIPPED": 0,
}


_PAYLOAD_KEYS = ("override_events", "events", "actions", "overrides")


def _read_actions(path: Path) -> list[dict[str, Any]]:
    """读覆盖/动作记录：JSON 数组、selection.json（带 override_events）或 JSONL。"""
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in _PAYLOAD_KEYS:
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
        if "item_id" in data:  # 只有一行的 JSONL：整份就是一个事件
            return [data]
    return []


def _item_event(
    kind: str,
    item_id: str,
    scores: dict[str, dict[str, Any]],
    *,
    delivered: bool,
    now: datetime,
) -> FeedbackEvent:
    row = scores.get(item_id)
    if row is None:
        raise SystemExit(f"在打分行里找不到 {item_id}：没有它就无法归因，拒绝凭猜测调参")
    summary = summarise(row["scores"])
    stamp = now.isoformat(timespec="seconds")
    return FeedbackEvent(
        event_id=_event_id(f"{kind}:{item_id}:{stamp}"),
        at=stamp,
        kind=kind,
        item_id=item_id,
        summary={key: summary[key] for key in ("direction_max", "policy_max", "quality", "exploration", "reduced", "score", "form")},
        delivered=delivered,
    )


def _statement_event(statement: str, now: datetime) -> FeedbackEvent:
    classification = classify(statement)
    stamp = now.isoformat(timespec="seconds")
    return FeedbackEvent(
        event_id=_event_id(f"STATEMENT:{statement}:{stamp}"),
        at=stamp,
        kind="STATEMENT",
        signal=None if classification.signal == "UNCLASSIFIED" else classification.signal,
        statement=statement,
        summary={"classification": classification.as_dict()},
    )


def _print_proposal(proposal) -> None:
    data = proposal.as_dict()
    print("接受:", "是" if data["accepted"] else "否", "|", data["reason"])
    print("调整前:", json.dumps(data["before"], ensure_ascii=False))
    print("调整后:", json.dumps(data["after"], ensure_ascii=False))
    if data["moved"]:
        print("位移:", json.dumps(data["moved"], ensure_ascii=False))
    if data["guard"]:
        print("回放:", json.dumps(data["guard"], ensure_ascii=False))
    if data["needs_agent"]:
        print("注意: 有反馈无法归类，需要 Agent 进一步解释后才能调参")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="用户反馈 → 参数调整（可审计、可回滚）")
    parser.add_argument("--user", required=True)
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--scores", default=None, help="打分行文件，用于把条目反馈归因到维度")
    parser.add_argument("--veto", action="append", default=[], help="用户说这条不该给我")
    parser.add_argument("--promote", action="append", default=[], help="用户说这条有用")
    parser.add_argument("--not-delivered", action="store_true", help="被评价的条目当时并未被选中")
    parser.add_argument("--say", action="append", default=[], help="用户的原话")
    parser.add_argument("--from-csv", default=None, help="从复核表 CSV 读标签，自动生成反馈事件")
    parser.add_argument("--from-actions", default=None, help="从行为记录 JSON/JSONL 读动作（OPENED/SAVED/DISMISSED/SKIPPED）")
    parser.add_argument("--from-overrides", default=None, help="从 Agent 覆盖记录生成反馈：selection.json（override_events）、JSON 数组或 JSONL")
    parser.add_argument("--export-labels", default=None, help="把复核表里的标签写成 labels.jsonl（供重新拟合先验）")
    parser.add_argument("--profile-id", default="ai", help="配合 --export-labels：这些标签属于哪个画像")
    parser.add_argument("--record-ledger", action="append", default=[], help="把这些打分行记进回放用的历史")
    parser.add_argument("--delivered", action="append", default=[], help="与 --record-ledger 搭配：标记哪些当时被选中")
    parser.add_argument("--apply", action="store_true", help="够数就提交新版本")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--rollback", type=int, default=None)
    args = parser.parse_args(argv)

    store = ParameterStore(args.base, args.user)
    now = datetime.now(timezone.utc)

    if args.history:
        for record in store.history():
            print(
                f"v{record['version']:04d}  {record.get('updated_at', '')}  {record.get('source', '')}  "
                f"{record.get('digest', '')}  {record.get('note', '')[:60]}"
            )
        return 0

    if args.rollback is not None:
        record = rollback(store, args.rollback, now=now)
        print(f"已回滚：新版本 v{record.version:04d} = v{args.rollback:04d} 的参数")
        print(json.dumps(record.parameters.as_dict(), ensure_ascii=False))
        return 0

    if args.record_ledger:
        scores = _load_scores(Path(args.scores)) if args.scores else {}
        delivered = set(args.delivered)
        rows = []
        for path_str in args.record_ledger:
            path = Path(path_str)
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                summary = summarise(row["scores"])
                rows.append(
                    {
                        "item_id": row["item_id"],
                        "profile_id": row.get("profile_id"),
                        "direction_max": summary["direction_max"],
                        "policy_max": summary["policy_max"],
                        "quality": summary["quality"],
                        "exploration": summary["exploration"],
                        "reduced": summary["reduced"],
                        "score": summary["score"],
                        "delivered": row["item_id"] in delivered,
                        "recorded_at": now.isoformat(timespec="seconds"),
                    }
                )
        count = record_ledger(store, rows)
        print(f"已记入回放历史 {count} 条")
        if not args.apply and not args.veto and not args.promote and not args.say:
            return 0

    has_input = bool(
        args.veto or args.promote or args.say or args.from_csv or args.from_actions or args.from_overrides
    )
    if args.show or not has_input:
        current = store.current()
        print(f"当前版本 v{current.version:04d}（{current.source}）")
        print(json.dumps(current.parameters.as_dict(), ensure_ascii=False, indent=2))
        print(f"已应用反馈 {current.events_applied} 条")
        if not args.apply:
            return 0

    scores = _load_scores(Path(args.scores)) if args.scores else {}
    events: list[FeedbackEvent] = []

    if args.from_overrides:
        if not scores:
            raise SystemExit("--from-overrides 需要同时给 --scores：没有打分行就无法归因")
        entries = _read_actions(Path(args.from_overrides))
        ledger_rows = []
        for entry in entries:
            item_id = entry.get("item_id")
            if item_id not in scores or "include" not in entry:
                continue
            include = bool(entry["include"])
            summary = summarise(scores[item_id]["scores"])
            ledger_rows.append(
                {
                    "item_id": item_id,
                    "verdict": "LIKED" if include else "DISLIKED",
                    "direction_max": summary["direction_max"],
                    "policy_max": summary["policy_max"],
                    "quality": summary["quality"],
                    "exploration": summary["exploration"],
                    "reduced": summary["reduced"],
                    "score": summary["score"],
                    "delivered": bool(entry.get("was_selected", include)),
                    "recorded_at": now.isoformat(timespec="seconds"),
                }
            )
            delivered = bool(entry.get("was_selected", include))
            if include and not delivered:
                events.append(_item_event("ITEM_PROMOTE", item_id, scores, delivered=False, now=now))
            elif not include and delivered:
                events.append(_item_event("ITEM_VETO", item_id, scores, delivered=True, now=now))
        if ledger_rows:
            record_ledger(store, ledger_rows)
        print(f"从覆盖记录读到 {len(ledger_rows)} 条，累计 {len(events)} 条待调整事件")

    if args.from_csv:
        if not scores:
            raise SystemExit("--from-csv 需要同时给 --scores：没有打分行就无法归因")
        import csv as _csv

        ledger_rows = []
        with Path(args.from_csv).open(encoding="utf-8-sig", newline="") as handle:
            for row in _csv.DictReader(handle):
                label = (row.get("label") or "").strip()
                if label not in {"0", "1"}:
                    continue
                item_id = row["item_id"]
                selected = (row.get("selected") or "").strip() == "1"
                summary = summarise(scores[item_id]["scores"])
                ledger_rows.append(
                    {
                        "item_id": item_id,
                        "verdict": "LIKED" if label == "1" else "DISLIKED",
                        "direction_max": summary["direction_max"],
                        "policy_max": summary["policy_max"],
                        "quality": summary["quality"],
                        "exploration": summary["exploration"],
                        "reduced": summary["reduced"],
                        "score": summary["score"],
                        "delivered": selected,
                        "recorded_at": now.isoformat(timespec="seconds"),
                    }
                )
                if label == "1" and not selected:
                    events.append(_item_event("ITEM_PROMOTE", item_id, scores, delivered=False, now=now))
                elif label == "0" and selected:
                    events.append(_item_event("ITEM_VETO", item_id, scores, delivered=True, now=now))
        if ledger_rows:
            record_ledger(store, ledger_rows)
        print(f"从复核表读到 {len(ledger_rows)} 条标签，其中 {len(events)} 条与系统判断不一致")
        if args.export_labels and ledger_rows:
            export_path = Path(args.export_labels)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            with export_path.open("w", encoding="utf-8") as handle:
                for row in ledger_rows:
                    handle.write(
                        json.dumps(
                            {
                                "item_id": row["item_id"],
                                "profile_id": args.profile_id,
                                "label": 1 if row["verdict"] == "LIKED" else 0,
                                "labeler": "USER",
                                "method": "CSV",
                                "labeled_at": now.isoformat(timespec="seconds"),
                            },
                            ensure_ascii=False,
                        )
                        + chr(10)
                    )
            print(f"标签已导出到 {export_path}（labeler=USER），可用于重新拟合先验")

    if args.from_actions:
        if not scores:
            raise SystemExit("--from-actions 需要同时给 --scores：没有打分行就无法归因")
        actions = _read_actions(Path(args.from_actions))
        ledger_rows = []
        skipped = 0
        for entry in actions:
            label = ACTION_TO_LABEL.get(str(entry.get("action", "")).upper())
            if label is None or entry.get("item_id") not in scores:
                skipped += 1
                continue
            item_id = entry["item_id"]
            summary = summarise(scores[item_id]["scores"])
            delivered = bool(entry.get("delivered", True))
            ledger_rows.append(
                {
                    "item_id": item_id,
                    "verdict": "LIKED" if label else "DISLIKED",
                    "direction_max": summary["direction_max"],
                    "policy_max": summary["policy_max"],
                    "quality": summary["quality"],
                    "exploration": summary["exploration"],
                    "reduced": summary["reduced"],
                    "score": summary["score"],
                    "delivered": delivered,
                    "recorded_at": now.isoformat(timespec="seconds"),
                }
            )
            if label == 1 and not delivered:
                events.append(_item_event("ITEM_PROMOTE", item_id, scores, delivered=False, now=now))
            elif label == 0 and delivered:
                events.append(_item_event("ITEM_VETO", item_id, scores, delivered=True, now=now))
        if ledger_rows:
            record_ledger(store, ledger_rows)
        print(f"从行为记录读到 {len(ledger_rows)} 条可用动作（跳过 {skipped} 条无法映射或缺少分数），"
              f"累计 {len(events)} 条待调整事件")

    for item_id in args.veto:
        events.append(_item_event("ITEM_VETO", item_id, scores, delivered=not args.not_delivered, now=now))
    for item_id in args.promote:
        events.append(_item_event("ITEM_PROMOTE", item_id, scores, delivered=not args.not_delivered, now=now))
    for statement in args.say:
        event = _statement_event(statement, now)
        events.append(event)
        if event.signal:
            print(f"识别: 「{statement}」→ {event.signal}")
        else:
            print(f"识别: 「{statement}」→ 无法归类（需要 Agent 解释，不会自动调参）")

    if events:
        append_pending(store, events)
    pending = load_pending(store)
    proposal = propose(store, pending, ledger=load_ledger(store), now=now)
    _print_proposal(proposal)

    if proposal.accepted and args.apply:
        record = commit(store, proposal, now=now)
        print(f"已提交 v{record.version:04d}  {record.parameters.digest()}")
    elif proposal.accepted:
        print("未提交（加 --apply 才会落盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
