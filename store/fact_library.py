"""事实库：已经还原过的事实留在这里，下次先用它做一次便宜的比对。

为什么需要它：事实还原要跑模型，每条几秒。全量跑一遍，几十条就是几分钟的 GPU 时间，
而绝大多数内容在第一轮粗筛就会被扔掉——为它们付费是浪费。

所以分成两段：

1. **便宜的一段**（不碰模型）：拿新条目的标题与锚点，去事实库里的**已有事实文本**上比。
   比得上一模一样 → 直接排除，这一步就是去重。
   比得上一部分 → 当成"可能是后续更新"，放行但打标记，让人多看一眼。
   比不上 → 新内容。
2. **贵的一段**（调模型）：只对通过粗筛、并且上面判为不是重复的条目做事实还原，
   结果写进事实库。

判断用的是"库里的说法"而不是"库里的条目标题"——因为同一件事的说法本来就该比标题更像。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

LATIN = re.compile(r"[A-Za-z][A-Za-z0-9._+\-]*")
CJK = re.compile("[\u4e00-\u9fff]{2,}")
SPACES = re.compile(r"\s+")

IDENTICAL = 0.8   # 库里的说法几乎全被覆盖 → 当成同一条
SIMILAR = 0.45    # 覆盖了一部分 → 可能是更新


def _split_tokens(text: str) -> tuple[set[str], set[str]]:
    """拉丁/数字词与 CJK 片段分开返回。

    为什么要分：CJK 二元组噪声大（「价格」「格不」「不变」全会算进去），
    把它们和 deepseek、v4.1 这种硬锚点混在一个分母里，比例会被稀释到看不出区别。
    有硬锚点时以硬锚点为准，没有才退回中文片段。
    """
    text = unicodedata.normalize("NFKC", text or "")
    latin: set[str] = set()
    cjk: set[str] = set()
    for match in LATIN.finditer(text):
        token = match.group(0).lower().strip("._+-")
        if len(token) >= 2:
            latin.add(token)
    for run in CJK.findall(text):
        for size in (2, 3):
            for index in range(len(run) - size + 1):
                cjk.add(run[index : index + size])
    return latin, cjk


def _tokens(text: str) -> set[str]:
    latin, cjk = _split_tokens(text)
    return latin | cjk


def _entry_text(entry: dict[str, Any]) -> str:
    parts = []
    for claim in entry.get("claims") or []:
        parts.extend(
            [str(claim.get("subject") or ""), str(claim.get("relation") or ""), str(claim.get("object") or "")]
        )
    return " ".join(parts)


def load(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + chr(10))
            count += 1
    return count


def lookup(item: dict[str, Any], library: list[dict[str, Any]]) -> dict[str, Any]:
    """不碰模型的一次比对。返回 {verdict, ratio, entry}。

    verdict:
      IDENTICAL —— 库里的说法几乎全被这段内容覆盖，当成同一条，直接排除
      SIMILAR   —— 覆盖了一部分，可能是后续更新，放行但要标记
      NEW       —— 库里有这一条的任何痕迹都没有
    """
    # 用和抽取时**同一段文字**来比。抽事实喂的是 summarise() 挑过的段落，
    # 这里如果只取正文前 300 字，就会拿半截去比全量，覆盖率永远不够。
    from judgment.text import summarise

    title = item.get("title") or ""
    body = item.get("text_clean") or item.get("text") or ""
    query = title + " " + summarise(body, title, 1200)
    query_latin, query_cjk = _split_tokens(query)
    if not query_latin and not query_cjk:
        return {"verdict": "NEW", "ratio": 0.0, "entry": None}

    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for entry in library:
        known_latin, known_cjk = _split_tokens(_entry_text(entry))
        if len(known_latin) >= 2:
            # 有硬锚点就只看硬锚点，中文片段只用来兜底
            ratio = len(query_latin & known_latin) / len(known_latin)
        else:
            known = known_latin | known_cjk
            if not known:
                continue
            ratio = len((query_latin | query_cjk) & known) / len(known)
        if ratio > best[0]:
            best = (ratio, entry)

    ratio, entry = best
    if ratio >= IDENTICAL:
        verdict = "IDENTICAL"
    elif ratio >= SIMILAR:
        verdict = "SIMILAR"
    else:
        verdict = "NEW"
    return {"verdict": verdict, "ratio": round(ratio, 3), "entry": entry}


def entry_for(item: dict[str, Any], claims: list[dict[str, Any]], *, now: str) -> dict[str, Any]:
    return {
        "item_id": item.get("item_id"),
        "title": item.get("title"),
        "canonical_url": item.get("canonical_url"),
        "claims": claims,
        "stored_at": now,
        "score": item.get("score"),
    }
