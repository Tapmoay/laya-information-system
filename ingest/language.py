"""语种识别（脚本启发式）。

它只做一件事：把"明显是中文 / 明显是英文 / 混着 / 说不清"分开。
说不清就返回 UNKNOWN，绝不猜（policy §0.1 第 2 条）。
"""

from __future__ import annotations

import re

HAN = re.compile(r"[\u4e00-\u9fff]")
LATIN_WORD = re.compile(r"[A-Za-z]+")

# 用"汉字数 vs 英文词数"而不是"汉字 vs 字母"：英文一个词平均五个字母，
# 按字母算会把正常中文文本误判成偏英文。
HAN_SHARE_ZH = 0.7
HAN_SHARE_EN = 0.3


def detect_language(text: str) -> dict:
    han = len(HAN.findall(text))
    latin_words = len(LATIN_WORD.findall(text))
    total = han + latin_words
    if total == 0:
        return {"tag": "UNKNOWN", "method": "UNKNOWN", "confidence": None}

    ratio = han / total
    if ratio >= HAN_SHARE_ZH:
        return {"tag": "zh-Hans", "method": "CONTENT_HEURISTIC", "confidence": round(min(0.99, ratio), 3)}
    if ratio <= HAN_SHARE_EN:
        return {"tag": "en", "method": "CONTENT_HEURISTIC", "confidence": round(min(0.99, 1 - ratio), 3)}
    return {"tag": "MIXED", "method": "CONTENT_HEURISTIC", "confidence": 0.5}
