"""把 final.md 解析成条目并渲染成可发送的 Markdown。

这里只做**文本**：不碰任何具体聊天工具的凭据或 API。
真实渠道（邮件、文件、你自己加的适配器）在 `tools/push.py` 里。

为什么单独拆出来：渠道会换，Markdown 的结构与「按 item_id 记账」不会换。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CIRCLED = "①②③④⑤⑥⑦⑧⑨"


def parse_final(path: Path) -> list[dict[str, Any]]:
    """从 final.md 抽条目。通道标题以 '## ' 开头，条目以 '### ' 开头。"""
    entries: list[dict[str, Any]] = []
    section = ""
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current:
                entries.append(current)
                current = None
            section = line[3:].strip()
        elif line.startswith("### "):
            if current:
                entries.append(current)
            raw = line[4:].strip()
            match = re.match(r"^\d+\.\s*(.*)$", raw)
            current = {
                "section": section,
                "title": match.group(1) if match else raw,
                "summary": "",
                "reason": "",
                "link": "",
                "labels": [],
                "item_id": "",
            }
        elif current is not None:
            if line.startswith("item_id："):
                current["item_id"] = line.split("：", 1)[1].strip()
            elif line.startswith("服务目标："):
                current["labels"] = [
                    part.strip() for part in line.replace("服务目标：", "").split("·") if part.strip()
                ]
            elif line.startswith("**AI 摘要**："):
                current["summary"] = line.split("：", 1)[1].strip()
            elif line.startswith("为什么选它："):
                current["reason"] = line.replace("为什么选它：", "").strip()
            elif "链接：" in line and not current["link"]:
                current["link"] = line.split("链接：", 1)[1].strip()
    if current:
        entries.append(current)
    return entries


def channel_label(section: str) -> str:
    if "通道 A" in section:
        return "🟦 A 目标驱动"
    if "通道 B" in section:
        return "🟨 B 变化监测"
    if "通道 C" in section:
        return "🟩 C 受控探索"
    return section


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


META_HINTS = ("日期：", "作者：", "阅读时间", "发布时间", "字数：", "标签：")

_HEADER = re.compile(
    r"^\s*(?:日期|作者|预计阅读时间|阅读时间|阅读时长|发布时间|更新时间|来源|标签|字数)"
    r"\s*[：:][^|｜]{0,40}[|｜]\s*"
)
_FILLER = re.compile(
    r"^\s*(?:最新发表|最新出版物|最新发布|刚刚发布|发布于|发表于|摘要|Abstract|摘要[:：])\s*[：:，,]?\s*"
)

SIGNAL_WORDS = (
    "发现", "表明", "显示", "提出", "证明", "暴露", "泄露", "泄漏", "修复", "发布",
    "结果", "方法", "漏洞", "攻击", "绕过", "失败", "降低", "提升", "首次", "可以",
)


def _clean(segment: str) -> str:
    text = (segment or "").strip()
    while True:
        stripped = _HEADER.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped.strip()
    text = _FILLER.sub("", text, count=1)
    text = text.lstrip("…… ").strip()
    if any(text.startswith(hint) for hint in META_HINTS):
        for cut in ("……", "…", "。"):
            if cut in text:
                text = text.split(cut, 1)[1]
                break
        else:
            if len(text) <= 24:
                return ""
    return text.lstrip("…… ").strip()


def _gist(text: str, limit: int = 76) -> str:
    raw = (text or "").replace("……", " … ").replace("…", " … ")
    segments = [_clean(seg) for seg in raw.split(" … ")]
    segments = [seg for seg in segments if seg]
    substantive = [
        seg for seg in segments
        if len(seg) >= 20 and not any(hint in seg for hint in META_HINTS)
    ]
    if not substantive:
        picked = segments[0] if segments else ""
    elif len(substantive) == 1:
        picked = substantive[0]
    else:
        def score(seg: str) -> float:
            hits = sum(1 for word in SIGNAL_WORDS if word in seg)
            return hits * 10 + min(len(seg), 120) / 12

        picked = max(substantive, key=score)
    picked = re.sub(r"\s+", " ", picked).strip(" “”\"")
    for cut in ("。", "！", "？"):
        if cut in picked:
            head = picked.split(cut)[0]
            if 10 <= len(head) <= limit:
                return "讲的是：" + head + "。"
    return "讲的是：" + _clip(picked, limit)


def _first_clause(text: str, limit: int = 64) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    for cut in ("——", "；", "。", "，"):
        if cut in text:
            head = text.split(cut)[0]
            if 4 <= len(head) <= limit:
                return head
    return _clip(text, limit)


def render(entries: list[dict[str, Any]]) -> list[str]:
    total = len(entries)
    messages = []
    for index, entry in enumerate(entries, 1):
        marker = CIRCLED[index - 1] if index <= len(CIRCLED) else "{}. ".format(index)
        lines = [
            "**{} {}**".format(marker, _clip(entry["title"], 40)),
            "{} · {}/{}".format(channel_label(entry["section"]), index, total),
        ]
        quoted = []
        if entry.get("summary"):
            quoted.append("> " + _gist(entry["summary"], 76))
        if entry.get("labels"):
            quoted.append("> 服务：" + " · ".join(entry["labels"]))
        if entry["reason"]:
            quoted.append("> 为什么给你：" + _first_clause(entry["reason"], 48))
        if quoted:
            lines.extend([""] + quoted)
        if entry["link"]:
            lines.extend(["", "[原文]({})".format(entry["link"])])
        messages.append("\n".join(lines))
    return messages


def batch_header(when=None, total: int = 0) -> str:
    from datetime import datetime as _dt

    stamp = (when or _dt.now()).strftime("%Y-%m-%d %H:%M")
    return "**{}** · 今日 {} 条".format(stamp, total) if total else "**{}**".format(stamp)


def with_header(messages: list[str], when=None) -> list[str]:
    if not messages:
        return messages
    gap = chr(10) + chr(10)
    head = batch_header(when, len(messages)) + gap
    return [head + messages[0]] + messages[1:]


def commit_ledger_items(final_path: Path, item_ids: set[str]) -> int:
    """按 item_id 记账：final.md 按通道分组，pending 按核心/探索排列，行号不可靠。"""
    from store.repeat_guard import DEFAULT_LEDGER, remember

    staging = final_path.parent / "ledger-pending.jsonl"
    if not staging.exists():
        return 0
    sent_rows: list[dict[str, Any]] = []
    kept_lines: list[str] = []
    for line in staging.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue
        if str(row.get("item_id") or "") in item_ids:
            sent_rows.append(row)
        else:
            kept_lines.append(line)
    written = remember(ROOT / DEFAULT_LEDGER, sent_rows) if sent_rows else 0
    if kept_lines:
        staging.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    else:
        staging.unlink(missing_ok=True)
    return written
