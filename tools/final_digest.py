"""把一次终选变成最终的阅读产物：中文标题 + AI 摘要 + 原文可回溯。

三个约定：

1. **语言不是筛选条件。** 一条内容值不值得进来，由 13 问测量与分数决定，
   和它是中文、英文还是日文无关。所以翻译只发生在这里——最后一步。
2. **AI 摘要只是摘要。** 它是模型把原文摘要译成中文并压缩，不添加原文没有的判断。
   它是"这个东西大致讲什么"，不是结论；要结论就点链接回原文。
3. **原文永远留着。** 每条都带原始标题、来源、链接与 item_id，
   任何一条都能顺着 provenance.jsonl 回到抓取那一刻。

用法：
    python tools/final_digest.py --selection runs/agent/blind4/selection.json \
        --items runs/crawl-mixed/items.json --out runs/final/2026-10-19
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.channels import CHANNELS, DEEP, SAVE, assign, label  # noqa: E402
from judgment.text import summarise as summarise_text  # noqa: E402
from store.repeat_guard import DEFAULT_LEDGER, check as guard_check, entry_for, load as load_ledger  # noqa: E402

DIGEST_CHARS = 320

PREAMBLE = [
    "> 这份产物只回答两件事：今天有什么、每一件大致讲什么。",
    "> **AI 摘要**是模型把原文摘要译成中文并压缩的结果——只做翻译与压缩，不添加原文没有的判断。",
    "> 要下结论请点链接回原文；每条都带 item_id，可顺着 provenance.jsonl 回到抓取那一刻。",
    "",
]


def _load(path: Path) -> Any:
    if not path.exists():
        print("错误：找不到 {}".format(path), file=sys.stderr)
        raise SystemExit(2)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _entry(
    item: dict[str, Any],
    picked: dict[str, Any],
    translation: dict[str, str] | None,
    claims: list[dict[str, Any]] | None = None,
    channel: str = "A",
) -> dict[str, Any]:
    title = (item.get("title") or "").split(" | ")[0].strip()
    body = item.get("text_clean") or item.get("text") or ""
    # 优先用终选者写的那一句：它读过了全文，比"头中尾各取一段"准得多。
    # 只有它没写时才退回机械摘要——那时候摘要质量差是已知的，不是意外。
    if (picked.get("gist") or "").strip():
        digest, translated = picked["gist"].strip(), True
    elif translation and translation.get("summary"):
        digest, translated = translation["summary"], True
    else:
        digest, translated = summarise_text(body, title, DIGEST_CHARS), False
    return {
        "item_id": item["item_id"],
        "title_zh": (translation or {}).get("title") or title,
        "original_title": title,
        "digest": digest,
        "digest_is_translation": translated,
        "original_language": item.get("detected_language") or item.get("declared_language"),
        "source_id": item.get("source_id"),
        "source_name": item.get("source_name"),
        "domain": item.get("domain"),
        "url": item.get("canonical_url"),
        "reason": picked.get("reason", ""),
        "score": picked.get("score"),
        "facts": claims or [],
        "channel": channel,
        "channel_label": CHANNELS.get(channel, ""),
        "labels": picked.get("labels") or [],
        "state": label(DEEP),
    }


def _md(entries: list[dict[str, Any]], saves: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# 本期读物", ""] + PREAMBLE
    lines.append("> 分三条通道，不是一条流：A 要能推进手上的事，B 防止漏掉外部变化，C 扩大发现面。")
    lines.append("")

    def block(group: list[dict[str, Any]]) -> None:
        for offset, entry in enumerate(group, 1):
            lines.append("### {}. {}".format(offset, entry["title_zh"]))
            lines.append("")
            if entry.get("facts"):
                lines.append("**事实**")
                lines.append("")
                for claim in entry["facts"]:
                    stamp = claim.get("time") or "-"
                    suffix = "" if stamp in ("-", "") else "（{}）".format(stamp)
                    lines.append(
                        "- {} {} {}{}".format(
                            claim.get("subject", ""), claim.get("relation", ""), claim.get("object", ""), suffix
                        )
                    )
                lines.append("")
            lines.append("**AI 摘要**：" + (entry["digest"] or "（原文过短，没有可压缩的内容）"))
            lines.append("")
            if entry["original_title"] and entry["original_title"] != entry["title_zh"]:
                lines.append(
                    "原文标题：{}（{}）".format(entry["original_title"], entry["original_language"] or "语言未知")
                )
            lines.append(
                "来源：{} / {}　·　链接：{}".format(
                    entry["source_name"] or entry["source_id"], entry["domain"], entry["url"]
                )
            )
            if entry.get("labels"):
                lines.append("服务目标：" + " · ".join(entry["labels"]))
            if entry["reason"]:
                lines.append("为什么选它：" + entry["reason"])
            lines.append("")
            lines.append("item_id：" + entry["item_id"])
            lines.append("")

    for channel in ("A", "B", "C"):
        group = [entry for entry in entries if entry["channel"] == channel]
        if not group:
            continue
        lines.append("## 通道 {}　{}".format(channel, CHANNELS.get(channel, "")))
        lines.append("")
        block(group)

    if saves:
        lines.append("## 值得暂存（{} 条）".format(len(saves)))
        lines.append("")
        lines.append("> 过线但这次没进前三通道的。只留链接和一句为什么，不展开——")
        lines.append("> 真要看的时候点进去，别让它们占掉现在的注意力。7 天内它们还会再参与一次挑选。")
        lines.append("")
        for row in saves:
            lines.append("- [{}]({})　·　{}".format(row["title"], row["url"], row["why"]))
        lines.append("")

    return "\n".join(lines) + "\n"

def _saves(path: Path, limit: int) -> list[dict[str, Any]]:
    """从候补池取"值得暂存"的条目：只留标题、链接、一句为什么。"""
    if not path.exists():
        return []
    from judgment.scoring import summarise as summarise_scores

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = row.get("item") or {}
        title = (item.get("title") or "").split(" | ")[0].strip()
        url = item.get("canonical_url")
        if not title or not url:
            continue
        why = "过线但这次没排上"
        scores = row.get("scores")
        if isinstance(scores, dict) and isinstance(scores.get("scores"), dict):
            summary = summarise_scores(scores["scores"])
            values = summary.get("values") or {}
            if values:
                axis = max(values, key=values.get)
                why = "{} 最高（{:+.2f}），综合 {:.2f}".format(axis, values[axis], summary["score"])
        rows.append({"title": title, "url": url, "why": why, "score": (scores or {}).get("score")})
    rows.sort(key=lambda r: -(float(r.get("score") or 0)))
    return rows[:limit]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="终选 → 中文标题与 AI 摘要")
    parser.add_argument("--selection", required=True, help="selection.json")
    parser.add_argument("--items", required=True, help="items.json（或抓取目录）")
    parser.add_argument("--translations", default=None, help="translations.json，没有则本地摘要原文")
    parser.add_argument("--out", required=True)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="已推送台账；传空字符串则不记账")
    parser.add_argument("--no-guard", action="store_true", help="跳过 7 天内去重（手工复盘用）")
    parser.add_argument("--no-record", action="store_true", help="只产出，不写台账")
    parser.add_argument("--scores", default=None, help="打分行；给了才能按三通道分组")
    parser.add_argument("--backlog", default="store/backlog.jsonl", help="暂存池；用于「值得暂存」一节")
    parser.add_argument("--save-limit", type=int, default=8)
    args = parser.parse_args(argv)

    raw_selection = _load(Path(args.selection))
    selection = raw_selection.get("selection") or raw_selection
    items_path = Path(args.items)
    if items_path.is_dir():
        items_path = items_path / "items.json"
    items = {item["item_id"]: item for item in _load(items_path)}

    translations: dict[str, dict[str, str]] = {}
    if args.translations:
        path = Path(args.translations)
        if path.is_dir():
            path = path / "translations.json"
        translations = _load(path)

    fact_rows: dict[str, list[dict[str, Any]]] = {}
    facts_path = items_path.parent / "facts.jsonl"
    if facts_path.exists():
        for line in facts_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            fact_rows[row["item_id"]] = row.get("claims") or []

    channels: dict[str, str] = {}
    if args.scores:
        from judgment.scoring import summarise as summarise_scores

        for line in Path(args.scores).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row.get("scores"), dict):
                summary = summarise_scores(row["scores"])
                channels[row["item_id"]] = assign(summary["values"])["channel"]

    blocked: list[dict[str, Any]] = []
    history = [] if args.no_guard else load_ledger(args.ledger)
    now = datetime.now(timezone.utc)

    def collect(group: str) -> list[dict[str, Any]]:
        out = []
        for picked in selection.get(group) or []:
            item = items.get(picked["item_id"])
            if item is None:
                print("警告：{} 不在 items.json 里".format(picked["item_id"]), file=sys.stderr)
                continue
            # 7 天内推过的东西不再出现：重复是噪声，不是提醒。
            if history:
                probe = dict(item)
                probe["score"] = picked.get("score")
                verdict = guard_check(probe, history, now=now)
                if verdict["decision"] == "BLOCK":
                    blocked.append({
                        "item_id": picked["item_id"],
                        "title": (item.get("title") or "")[:60],
                        "reason": verdict["reason"],
                        "age_days": verdict["age_days"],
                    })
                    continue
            channel = channels.get(picked["item_id"]) or ("A" if group == "core" else "C")
            out.append(
                _entry(
                    item,
                    picked,
                    translations.get(picked["item_id"]),
                    fact_rows.get(picked["item_id"]),
                    channel,
                )
            )
        return out

    entries = collect("core") + collect("exploration")
    saves = _saves(Path(args.backlog), args.save_limit)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": entries,
        "saved": saves,
        "translated": sum(1 for entry in entries if entry["digest_is_translation"]),
        "total": len(entries),
        "by_channel": {
            channel: sum(1 for entry in entries if entry["channel"] == channel) for channel in ("A", "B", "C")
        },
        "note": "AI 摘要 = 原文摘要的中文译文或压缩，不添加原文没有的判断；原文链接与 item_id 保留",
    }
    if blocked:
        print("压住 {} 条（7 天内推过）：".format(len(blocked)), file=sys.stderr)
        for row in blocked:
            print("  - {} … {}".format(row["title"], row["reason"]), file=sys.stderr)

    (out / "final.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    (out / "final.md").write_text(_md(entries, saves), encoding="utf-8")
    print("写好了：{} 条（A {} / B {} / C {}），其中 {} 条是译文".format(
        payload["total"], payload["by_channel"]["A"], payload["by_channel"]["B"],
        payload["by_channel"]["C"], payload["translated"]))
    if args.ledger and not args.no_record:
        kept = {entry["item_id"] for entry in entries}
        ledger_rows = []
        for picked in (selection.get("core") or []) + (selection.get("exploration") or []):
            if picked["item_id"] not in kept:
                continue
            item = dict(items.get(picked["item_id"]) or {})
            if not item:
                continue
            item["score"] = picked.get("score")
            ledger_rows.append(entry_for(item, now=now))
        # 不直接落账：推送可能失败，落了账这 7 条会在 7 天内不再出现。
        # 先写待提交清单，推送成功之后再落账。
        staging = out / "ledger-pending.jsonl"
        staging.write_text(
            chr(10).join(json.dumps(row, ensure_ascii=False) for row in ledger_rows) + chr(10),
            encoding="utf-8",
        )
        print("记账待提交 {} 条 → {}（推送成功后落账）".format(len(ledger_rows), staging))

    print("输出目录:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

