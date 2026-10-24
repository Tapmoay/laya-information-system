"""去掉"每页都有"的导航与页脚。

为什么需要：抓到的不等于读到的。midifan 这类站的正文前面有一大段
"登陆账号 / 站内信 / 厂商大全 / A B C …"，模型读到的是这些，
于是"设备评测"被判断成"产品广告"——这不是模型判错，是我们喂错了。

方法不靠站点适配，而靠**同一来源多页重复**：一页里反复出现的行是导航，
只在某页出现的行才是内容。这在离线建集时最可靠（同源 10 条就能算出来），
也可以把结果落成每个来源的"chrome 清单"，线上单页抓取时直接套用。

确定性：同样的页集合必须算出同样的清单，所以指纹只做规范化，
不做模糊匹配——模糊匹配会让"清单"随阈值漂移。
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

WHITESPACE = re.compile(r"\s+")
MIN_FINGERPRINT_CHARS = 2
MIN_LINE_CHARS = 2


def fingerprint(line: str) -> str:
    return WHITESPACE.sub(" ", line).strip().casefold()


def split_lines(text: str) -> list[str]:
    return [line for line in (text or "").split(chr(10)) if line.strip()]


def chrome_lines(
    pages: Sequence[str],
    *,
    min_share: float = 0.6,
    min_pages: int = 4,
    min_chars: int = MIN_FINGERPRINT_CHARS,
) -> list[str]:
    """找出在大多数页面都出现的行。返回按字母序排好的指纹，方便比对与落盘。"""
    if len(pages) < min_pages:
        return []
    counts: dict[str, int] = {}
    for page in pages:
        seen = {fingerprint(line) for line in split_lines(page)}
        for item in seen:
            if len(item) < min_chars:
                continue
            counts[item] = counts.get(item, 0) + 1
    threshold = max(min_pages, int(len(pages) * min_share + 0.999))
    return sorted(item for item, count in counts.items() if count >= threshold)


def strip_chrome(text: str, chrome: Iterable[str], *, min_chars: int = MIN_LINE_CHARS) -> str:
    """删掉命中清单的行；剩下的拼回去，保持原有顺序。"""
    blocked = {fingerprint(item) for item in chrome}
    kept = [
        line
        for line in split_lines(text)
        if fingerprint(line) not in blocked and len(line.strip()) >= min_chars
    ]
    return chr(10).join(kept)


def apply(pages: Sequence[str], *, min_pages: int = 4) -> dict[str, object]:
    """对一组同源页面做一次：算清单 → 逐页削 → 报告压缩比。"""
    chrome = chrome_lines(pages, min_pages=min_pages)
    cleaned = [strip_chrome(page, chrome) for page in pages]
    return {
        "chrome": chrome,
        "cleaned": cleaned,
        "shrink": [shrink_ratio(page, clean) for page, clean in zip(pages, cleaned)],
    }


def shrink_ratio(before: str, after: str) -> float:
    original = len(WHITESPACE.sub(" ", before or ""))
    if not original:
        return 0.0
    return round(1.0 - len(WHITESPACE.sub(" ", after or "")) / original, 4)
