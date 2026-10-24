"""把一批真实抓取的内容，按"每天来一批"的方式模拟运行 N 天。

为什么这样模拟：真等两周不现实，但**每天只看到新到的一批**这个约束是真的。
所以按来源轮转把池子排成到达顺序，每天切一段，走完整的
  合并候补 → 选 5+2 → 记账 → 收候补
流程。选择用确定性规则（不是 agent 终选），理由由最高价值维度生成，
所以这份产物用来验"机制跑不跑得通 + 内容质量如何"，不代表 agent 的判断质量。

    python tools/simulate_days.py --items runs/sim14-pool --scores runs/sim14-scores.jsonl \
        --days 14 --per-day 10 --out runs/sim14
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.channels import CHANNELS, assign, threshold_for  # noqa: E402
from judgment.scoring import rank, select, summarise  # noqa: E402
from judgment.text import summarise as summarise_text  # noqa: E402
from store.backlog import carry, merge  # noqa: E402
from store.repeat_guard import entry_for  # noqa: E402

THRESHOLD = 0.50
DEEP = 0.60


def interleave(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按来源轮转排成"到达顺序"，避免某一天全是 arXiv、另一天全是设计站。"""
    queues: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        queues.setdefault(item.get("source_id") or "?", []).append(item)
    order: list[dict[str, Any]] = []
    while any(queues.values()):
        for key in list(queues):
            if queues[key]:
                order.append(queues[key].pop(0))
            else:
                del queues[key]
    return order


def scores_of(path: Path) -> dict[str, dict[str, Any]]:
    table = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        table[row["item_id"]] = row
    return table


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="按天模拟运行")
    parser.add_argument("--items", required=True)
    parser.add_argument("--scores", required=True)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--per-day", type=int, default=10)
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", default="2026-10-20T09:00:00+08:00")
    args = parser.parse_args(argv)

    items_path = Path(args.items)
    if items_path.is_dir():
        items_path = items_path / "items.json"
    pool = json.loads(items_path.read_text(encoding="utf-8-sig"))
    table = scores_of(Path(args.scores))
    pool = [item for item in pool if item["item_id"] in table]
    stream = interleave(pool)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(args.start)

    backlog: list[dict[str, Any]] = []
    delivered: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    cursor = 0

    for day in range(1, args.days + 1):
        now = start + timedelta(days=day - 1)
        batch = stream[cursor : cursor + args.per_day]
        cursor += args.per_day
        if not batch:
            break

        merged = merge(batch, [table[i["item_id"]] for i in batch], backlog, now=now)
        candidates = []
        for item in merged["items"]:
            row = table.get(item["item_id"])
            if not row:
                continue
            summary = summarise(
                row["scores"],
                age_days=None,
                text=item.get("text_clean") or item.get("text") or "",
                title=item.get("title") or "",
            )
            channel = assign(summary["values"], form=summary["form"], text=item.get("text_clean") or item.get("text") or "")
            if summary.get("hard_drop"):
                continue  # 招聘/活动/版本号/引语：不进入任何通道
            if summary["score"] < threshold_for(channel["channel"]):
                continue  # 每条通道有各自的线
            candidates.append({
                "item_id": item["item_id"],
                "title": (item.get("title") or "").split(" | ")[0],
                "source_id": item.get("source_id"),
                "score": summary["score"],
                "reduced": summary["reduced"],
                "values": summary["values"],
                "channel": channel["channel"],
                "channel_scores": channel["scores"],
                "commercial": channel.get("commercial", False),
                "body": item.get("text_clean") or item.get("text") or "",
                "from_backlog": bool(item.get("from_backlog")),
                "backlog_attempts": item.get("backlog_attempts", 0),
                "url": item.get("canonical_url"),
            })

        # 候选已经按各自通道的线筛过，这里不能再套全局线——
        # 否则 C 通道的门槛等于没生效（0.42 的条目在 select 里又被 0.50 砍掉）。
        chosen = select(rank(candidates), threshold=0.0)
        picked = chosen["core"] + chosen["exploration"]
        picked_ids = {row["item_id"] for row in picked}

        day_records = []
        for rank_index, row in enumerate(picked, 1):
            gist = summarise_text(row["body"], row["title"], 120)
            reason = "最高维度 {}（{:+.2f}）".format(
                max(row["values"], key=row["values"].get), max(row["values"].values())
            )
            day_records.append({
                "rank": rank_index,
                "slot": "core" if rank_index <= 5 else "exploration",
                "item_id": row["item_id"],
                "title": row["title"],
                "source_id": row["source_id"],
                "score": round(row["score"], 3),
                "channel": row["channel"],
                "channel_scores": row["channel_scores"],
                "gist": gist,
                "reason": reason,
                "from_backlog": row["from_backlog"],
                "attempts": row["backlog_attempts"],
                "url": row["url"],
            })
            delivered.append(entry_for({"item_id": row["item_id"], "canonical_url": row["url"],
                                        "score": row["score"], "title": row["title"]}, now=now))

        carried = carry(
            backlog,
            items=[{k: v for k, v in item.items() if k != "candidate"} for item in merged["items"]],
            scores=[{**table[item["item_id"]], "score": next(
                (c["score"] for c in candidates if c["item_id"] == item["item_id"]), 0.0)}
                for item in merged["items"] if item["item_id"] in table],
            selected=picked_ids,
            threshold=THRESHOLD,
            now=now,
        )
        backlog = carried["rows"]

        days.append({
            "day": day,
            "date": now.date().isoformat(),
            "new": len(batch),
            "revived": merged["revived"],
            "candidates": len(candidates),
            "picked": day_records,
            "backlog": len(backlog),
        })

    # ── 汇总
    lines = ["# 14 天模拟推送", "",
             "> 内容来自一次真实抓取（{} 条进池）。按来源轮转排成到达顺序，每天切 {} 条新内容，".format(len(pool), args.per_day),
             "> 走完整流程：合并候补 → 选 5+2 → 记账 → 收候补。",
             "> 选择用确定性规则（不是 agent 终选），理由由最高价值维度自动生成——",
             "> 所以这份产物验的是**机制与内容质量**，不代表 agent 的判断质量。", ""]
    total = 0
    lines.append("| 天 | 日期 | 新到 | 候补复活 | 候选 | 推出 | 候补剩余 |")
    lines.append("|---|---|---|---|---|---|---|")
    for entry in days:
        total += len(entry["picked"])
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            entry["day"], entry["date"], entry["new"], len(entry["revived"]),
            entry["candidates"], len(entry["picked"]), entry["backlog"]))
    lines.append("")
    lines.append("合计推出 **{}** 条。".format(total))
    lines.append("")
    for entry in days:
        lines.append("## 第 {} 天（{}）".format(entry["day"], entry["date"]))
        lines.append("")
        if entry["revived"]:
            lines.append("> 候补复活 {} 条".format(len(entry["revived"])))
            lines.append("")
        for row in entry["picked"]:
            tag = "{}｜{:.2f}".format(row["channel"], row["score"])
            suffix = ""
            if row["from_backlog"]:
                suffix += "（候补第 {} 次）".format(row["attempts"])
            if row.get("commercial"):
                suffix += "（商业内容，仅标记）"
            lines.append("**{}. [{}] {}** {}".format(row["rank"], tag, row["title"], suffix))
            lines.append("")
            lines.append("- 来源：{}　通道分 A{:.2f}/B{:.2f}/C{:.2f}".format(
                row["source_id"], row["channel_scores"]["A"], row["channel_scores"]["B"], row["channel_scores"]["C"]))
            lines.append("- 讲了什么：" + (row["gist"] or "（无）"))
            lines.append("- 为什么：" + row["reason"])
            lines.append("- " + (row["url"] or ""))
            lines.append("")
    (out / "simulation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "simulation.json").write_text(
        json.dumps({"days": days, "delivered": delivered, "total": total}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("模拟 {} 天，推出 {} 条 → {}".format(len(days), total, out / "simulation.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
