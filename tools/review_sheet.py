"""把打分行导出成一张可以直接填的复核表。

为什么要有这个文件：参数的正确性最终取决于「这个人到底要不要这条」，
而这个判断只有人能给。所以要有一张表，让人五分钟能标完，
并且标完的东西能直接被 tools/learn.py 读走。

表里必须带摘要：只有标题的话，标注人根本判断不了内容值不值得看，
而用标题去猜内容，和让模型只看标题是同一个错误。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.params import PRIOR, Parameters  # noqa: E402
from judgment.scoring import rank, select, summarise  # noqa: E402
from judgment.text import summarise as summarise_text  # noqa: E402

COLUMNS = ["item_id", "domain", "score", "selected", "label", "title", "url", "excerpt"]
EXCERPT_CSV_CHARS = 260
EXCERPT_MD_CHARS = 320
NOTE_PREFIX = "**我的记录**："
TICK = chr(96)
FENCE = TICK * 3


def load_bodies(items_path: Path) -> dict[str, dict[str, str]]:
    """item_id → {title, body}：摘要来自正文，不来自标题。"""
    bodies: dict[str, dict[str, str]] = {}
    if not items_path.exists():
        return bodies
    for item in json.loads(items_path.read_text(encoding="utf-8-sig")):
        bodies[item["item_id"]] = {
            "title": item.get("title") or "",
            "body": item.get("text_clean") or item.get("text") or "",
        }
    return bodies


def build_rows(scores_path: Path, bodies: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    rows = []
    for line in scores_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        provenance = row.get("provenance") or {}
        summary = summarise(row["scores"])
        body = bodies.get(row["item_id"]) or {}
        title = body.get("title") or provenance.get("title") or ""
        rows.append(
            {
                "item_id": row["item_id"],
                "profile_id": row.get("profile_id", ""),
                "domain": provenance.get("domain", ""),
                "title": title,
                "url": provenance.get("canonical_url") or "",
                "excerpt": summarise_text(body.get("body", ""), title, EXCERPT_CSV_CHARS),
                **summary,
            }
        )
    return rows


def load_notes(md_path: Path) -> dict[str, list[str]]:
    """从上一版复核表里把用户写的备注按 item_id 捞出来。

    重新生成表会覆盖文件——如果不同时把备注带过去，等于把别人的工作删了。
    """
    import re

    notes: dict[str, list[str]] = {}
    if not md_path.exists():
        return notes
    text = md_path.read_text(encoding="utf-8")
    for block in re.split(r"(?m)^### ", text)[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        header = lines[0]
        item_id = ""
        match = re.search(r"item-[0-9a-zA-Z]+", header)
        if match:
            item_id = match.group(0)
        if not item_id:
            continue
        extra = [
            line.strip()
            for line in lines[1:]
            if line.strip()
            and not line.startswith("**")
            and not line.startswith("> ")
            and not line.startswith("链接：")
        ]
        if extra:
            notes[item_id] = extra
    return notes


def render_context(demand_path: Path) -> list[str]:
    from judgment.questions import slot_values

    demand = json.loads(demand_path.read_text(encoding="utf-8-sig"))
    slots = slot_values(demand)
    names = {
        "primary": "主目标（长期方向）",
        "secondary": "第二方向",
        "exploration": "探索位（跨领域，不属于任何固定学科）",
        "task": "当前任务",
    }
    lines = ["## 这次标注依据的四条方向", ""]
    for key in ("primary", "secondary", "exploration", "task"):
        lines.append("- **" + names[key] + "**：" + (slots.get(key) or "（空）"))
    lines += [
        "",
        "## 打标尺",
        "",
        "只回答一个问题：**这条内容，我要不要？** 不要按「质量好不好」标——质量由另一层负责。",
        "",
        "| 标 | 含义 |",
        "|---|---|",
        "| 1 | 出现在本周 5+2 里，我会觉得赚到了 |",
        "| 0 | 不想看到它 |",
        "| 留空 | 我也拿不准（该条保留系统原有判断） |",
        "",
        "标 1 的四种理由，任一成立即可：",
        "",
        "1. 能拿去改我的 agent 架构 / 契约 / 评测 / 工具链；",
        "2. 能直接用于把 LaYA 推到日常可运行；",
        "3. 能给设计语言、构图、光影、叙事节奏提供参考；",
        "4. 提供了「设计 / 构成 / 叙事」在别处（游戏美术、建筑、生物结构…）的化身。",
        "",
        "标 0 的典型：广告 / 招聘 / 采购 / 大会报名；只有新闻流水而没有可迁移的方法；跟上面四条都不沾边。",
        "",
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="导出复核表（CSV + Markdown）")
    parser.add_argument("--scores", default="calibration/scores-crawl-ai.jsonl")
    parser.add_argument("--items", default="runs/crawl-20261018T080741Z/items.json",
                        help="抓取产物的 items.json：摘要从这里取")
    parser.add_argument("--demand", default=None, help="需求快照：把四条方向槽写进表头")
    parser.add_argument("--out", default="calibration/review-crawl", help="输出前缀，不带扩展名")
    parser.add_argument("--top", type=int, default=0, help="只导出前 N 条（默认全部）")
    parser.add_argument("--carry-notes", default=None, help="上一版复核表：把用户写的备注带到新表里")
    parser.add_argument("--prefill", default=None, help="已确定的标签 jsonl：预填 label 列")
    args = parser.parse_args(argv)

    bodies = load_bodies(Path(args.items))
    if not bodies:
        print("警告：找不到 items.json，摘要列会是空的", file=sys.stderr)
    rows = build_rows(Path(args.scores), bodies)
    picked = select(rank(rows), threshold=Parameters(**PRIOR).threshold)
    chosen = {item.get("item_id") for item in picked["core"] + picked["exploration"]}
    for row in rows:
        row["selected"] = 1 if row["item_id"] in chosen else 0
    if args.prefill:
        known = {}
        for line in Path(args.prefill).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                record = json.loads(line)
                known[record["item_id"]] = record["label"]
        for row in rows:
            if row["item_id"] in known:
                row["label"] = str(known[row["item_id"]])
    notes = load_notes(Path(args.carry_notes)) if args.carry_notes else {}
    rows = sorted(rows, key=lambda row: (-row["score"], row["item_id"]))
    if args.top:
        rows = rows[: args.top]

    prefix = Path(args.out)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    csv_path = prefix.with_suffix(".csv")
    # 表可能正在 Excel 里开着；被占用时换一个名字，别让整次导出失败
    for suffix in ("", "-new", "-new2"):
        candidate = Path(str(prefix) + suffix).with_suffix(".csv") if suffix else csv_path
        try:
            candidate.open("a", encoding="utf-8-sig").close()
            candidate.unlink(missing_ok=True)
            csv_path = candidate
            prefix = Path(str(prefix) + suffix) if suffix else prefix
            break
        except PermissionError:
            continue
    else:
        print("所有候选文件名都被占用（Excel 可能正开着），请先关闭再重试", file=sys.stderr)
        return 2
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {**row, "label": row.get("label", ""), "score": "{:.3f}".format(row["score"])}
            )

    md: list[str] = ["# 复核表", ""]
    md.append("填法：在 " + TICK + "label" + TICK + " 列写 " + TICK + "1" + TICK + "（要）或 "
              + TICK + "0" + TICK + "（不要），留空表示不判断。")
    md += ["", "填完执行：", "", FENCE,
           "python tools/learn.py --user me --scores " + args.scores
           + " --from-csv " + str(csv_path).replace(chr(92), "/")
           + " --export-labels calibration/labels-user-ai.jsonl",
           FENCE, ""]
    if args.demand:
        md += render_context(Path(args.demand))
    md += ["## " + str(len(rows)) + " 条待标内容", ""]
    md.append("每条都带摘要——不用去点链接，也不用靠标题猜内容。")
    md.append("")
    for index, row in enumerate(rows, 1):
        flag = "**系统当前会推给你**" if row["selected"] else "系统没选"
        md.append("### " + str(index) + ". " + row["item_id"] + " · " + row["domain"]
                  + " · 分数 " + "{:.3f}".format(row["score"]) + " · " + flag)
        md.append("")
        md.append("**" + (row["title"] or "（无标题）") + "**")
        md.append("")
        summary = summarise_text(bodies.get(row["item_id"], {}).get("body", ""), row["title"], EXCERPT_MD_CHARS)
        md.append("> " + (summary or "（没有抓到正文摘要）"))
        md.append("")
        if row.get("url"):
            md.append("链接：" + row["url"])
        carried = notes.get(row["item_id"]) or []
        if carried:
            md.append("")
            for line in carried:
                md.append(NOTE_PREFIX + line)
        md.append("")
    prefix.with_suffix(".md").write_text(chr(10).join(md) + chr(10), encoding="utf-8")

    print("写入", csv_path, "与", prefix.with_suffix(".md"), "共", len(rows), "行")
    print("系统当前选中", sum(row["selected"] for row in rows), "条")
    missing = sum(1 for row in rows if not row["excerpt"])
    if missing:
        print("警告：", missing, "条没有摘要")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())