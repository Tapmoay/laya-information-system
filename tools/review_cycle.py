"""两周复盘：把这段时间真推给你的东西列出来，让你自己判它有没有用。

为什么需要：系统能算出"分数"，但算不出"这条到底有没有帮我做出更好的判断"。
那个只能由人回头看。所以每两周导出一次，三栏勾选：

    帮到了 / 只是一时兴趣 / 其实错过了什么

第三栏尤其重要：它记录的是**漏掉的东西**——这是任何自动系统都发现不了的，
只有你知道"我这两天本来该看到某个东西，但它没出现"。

用法：
    python tools/review_cycle.py --days 14 --out runs/review/cycle-2026-11-03.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build(rows: list[dict[str, Any]], *, now: datetime, days: int) -> dict[str, Any]:
    cutoff = now - timedelta(days=days)
    recent = [row for row in rows if (_parse(row.get("delivered_at")) or cutoff) >= cutoff]
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in recent:
        stamp = _parse(row.get("delivered_at"))
        key = stamp.strftime("%Y-%m-%d") if stamp else "未知日期"
        by_day.setdefault(key, []).append(row)
    return {"total": len(recent), "by_day": dict(sorted(by_day.items(), reverse=True))}


def render(data: dict[str, Any], *, days: int) -> str:
    lines = [
        "# 两周复盘",
        "",
        "> 这段时间推给你的 {} 条，挨个问三句：".format(data["total"]),
        "> 1. 它有没有帮我做出更好的判断、推进手上的事、或者建立能力？",
        "> 2. 它是不是只带来了一时兴趣？",
        "> 3. 有没有什么我本来该看到、但系统没给我的？",
        "",
        "把第 3 条的答案写给系统，那是唯一能修的地方。",
        "",
    ]
    for day, rows in data["by_day"].items():
        lines.append("## " + day)
        lines.append("")
        for row in rows:
            title = (row.get("title") or "")[:70]
            url = row.get("canonical_url") or ""
            lines.append("- [ ] {}".format(title))
            lines.append("      {}　{}".format(url, row.get("event_key") or ""))
            lines.append("      帮到了 / 一时兴趣 / 不该推")
            lines.append("")
    lines.append("## 漏掉的（我本来该看到，但系统没给）")
    lines.append("")
    lines.append("- ")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="导出两周复盘清单")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--ledger", default="store/delivered.jsonl")
    parser.add_argument("--out", required=True)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    rows = _load(Path(args.ledger))
    data = build(rows, now=now, days=args.days)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(data, days=args.days), encoding="utf-8")
    print("最近 {} 天共 {} 条 → {}".format(args.days, data["total"], target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
