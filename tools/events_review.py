"""回看"重大事件"到底重不重大。

每日终选会往 `store/event-log.jsonl` 写两样东西：事件候选（环境价值高的那几条），
以及被 agent 判成重大事件的条目。跑几天之后，这一页回答三个问题：

1. 事件候选进了多少条？其中几条真的被判成重大事件？
2. 被判成重大的都是什么？理由写的是"改变了什么判断"，还是"这条很重要"？
3. 有没有候选连续几天都没被选中——如果有，说明这个位置在制造噪音。

    python tools/events_review.py
    python tools/events_review.py --days 3
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "store" / "event-log.jsonl"


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarise(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_date[str(row.get("date") or "UNKNOWN")].append(row)
    return sorted(by_date.items(), reverse=True)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="回看重大事件候选与判定")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)

    rows = load(Path(args.log))
    if not rows:
        print("还没有事件日志：等第一次 --apply 之后会写进", args.log)
        return 0

    days = summarise(rows)[: args.days]
    total_candidates = sum(1 for row in rows if row.get("event_candidate"))
    total_major = sum(1 for row in rows if row.get("major"))
    for date, day_rows in days:
        candidates = [row for row in day_rows if row.get("event_candidate")]
        majors = [row for row in day_rows if row.get("major")]
        print(
            "{}  事件候选 {} · 判为重大 {} · 选中 {}".format(
                date, len(candidates), len(majors), sum(1 for row in day_rows if row.get("picked"))
            )
        )
        for row in majors:
            print("  [重大] {:.2f}  {}".format(float(row.get("score") or 0), row.get("title", "")))
            print("         改变了什么：{}".format(row.get("event_reason") or "（没写）"))
        for row in candidates:
            if row.get("major"):
                continue
            mark = "选中" if row.get("picked") else "未选"
            print("  [候选] {:.2f}  {}（{}）".format(float(row.get("score") or 0), row.get("title", ""), mark))
        print()

    print("合计：事件候选 {} 条，判为重大 {} 条".format(total_candidates, total_major))
    if total_candidates:
        print("事件位命中率：{:.0%}".format(total_major / total_candidates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
