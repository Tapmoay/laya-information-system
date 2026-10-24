"""把用户的一句话归到固定信号词表里。

这一步是"理解"，不是"调参"。它必须是确定性的、可复现的、可解释的：
为什么把这句话理解成"太窄"，要能指着某几个词说出来。
归不进任何一类的，就明确说"不知道"——不要让最接近的那一类顶替，
否则用户一句抱怨会被悄悄翻译成一次错误调参。
"""

from __future__ import annotations

from dataclasses import dataclass

PATTERNS: dict[str, tuple[str, ...]] = {
    "TOO_SHALLOW": ("太水", "水文", "没营养", "空话", "泛泛", "质量差", "深度不够", "鸡汤", "标题党"),
    "TOO_NARROW": ("太窄", "总是", "老是", "同类", "茧房", "没新意", "换个方向", "重复", "一个样"),
    "OFF_TOPIC": ("跑题", "无关", "不相关", "跟我做", "不是我要", "偏了", "不搭"),
    "AD_LIKE": ("广告", "软文", "招聘", "推销", "营销号", "带货", "推广"),
    "TOO_STRICT": ("漏掉", "错过", "太少", "太保守", "该给我", "没给"),
    "TOO_MUCH": ("太多", "看不过来", "精简", "少一点", "刷屏"),
}

UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True)
class Classification:
    signal: str
    matched: tuple[str, ...]
    candidates: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"signal": self.signal, "matched": list(self.matched), "candidates": list(self.candidates)}


def classify(statement: str) -> Classification:
    """命中数最多者胜；并列或零命中一律 UNCLASSIFIED。"""
    text = (statement or "").strip().lower()
    if not text:
        return Classification(UNCLASSIFIED, (), ())
    hits: list[tuple[str, tuple[str, ...]]] = []
    for signal, words in PATTERNS.items():
        matched = tuple(word for word in words if word in text)
        if matched:
            hits.append((signal, matched))
    if not hits:
        return Classification(UNCLASSIFIED, (), ())
    hits.sort(key=lambda item: (-len(item[1]), item[0]))
    best_signal, best_matched = hits[0]
    if len(hits) > 1 and len(hits[1][1]) == len(best_matched):
        return Classification(UNCLASSIFIED, best_matched, tuple(signal for signal, _ in hits))
    return Classification(best_signal, best_matched, (best_signal,))
