"""多语言 embedding：只在**跨语言**时用。

照抄的对象是一份 17 语言新闻聚合器的工程复盘，它用「多语言 embedding + KNN + union-find」
解决"同一条事件被四种语言报道、shingle Jaccard 恒为 0"的问题。

但直接照搬到我们的语料上会出事。实测（200 条中文内容，跨源两两比较）：

| 情形 | 余弦 |
|---|---|
| 同一篇加编者按 | 0.904 |
| 中文改写（同一件事换说法） | 0.680 |
| 跨语言同事件（英↔中） | 0.964 / 0.965 |
| 不相关 | 最高 **0.889** |
| 同主题不同事件（两篇郑钦文） | 0.754 |

也就是说：**同语言里，"不相关"能到 0.889，而"改写"只有 0.68——顺序是反的。**
这不是模型差，是中文营销/设计媒体的用词高度同质，向量被文风而不是事件主导。

跨语言则完全不同：不相关最高 0.672（p99 只有 0.052），同事件 0.96。间隔极宽。

所以这里的定位很明确：**embedding 只用于跨语言判定，不用于同语言。**
同语言继续用锚点与 shingle Jaccard——那两层在我们的语料上实测零误并。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Sequence

MODEL_DIR = "models/paraphrase-multilingual-MiniLM-L12-v2"
CROSS_LINGUAL_THRESHOLD = 0.90
_LOCK = threading.Lock()
_CACHE: dict[str, object] = {}


def available() -> bool:
    return Path(MODEL_DIR).exists()


def _load():
    """懒加载：没装模型时这套系统必须还能跑，不能因为缺一个可选件就起不来。"""
    global _CACHE
    with _LOCK:
        if "model" in _CACHE:
            return _CACHE["model"], _CACHE["tokenizer"]
        import torch  # noqa: F401  （延迟导入，没装 torch 也不影响其它路径）
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
        model = AutoModel.from_pretrained(MODEL_DIR)
        model.eval()
        _CACHE["tokenizer"] = tokenizer
        _CACHE["model"] = model
        return model, tokenizer


def encode(texts: Sequence[str], *, batch: int = 32) -> list[list[float]]:
    """平均池化 + L2 归一化。返回 list 而不是 ndarray，方便直接落盘。"""
    import torch

    model, tokenizer = _load()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch):
        chunk = list(texts[start : start + batch])
        batch_inputs = tokenizer(
            chunk, padding=True, truncation=True, max_length=256, return_tensors="pt"
        )
        with torch.no_grad():
            output = model(**batch_inputs)
        mask = batch_inputs["attention_mask"].unsqueeze(-1).float()
        pooled = (output.last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
        normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
        vectors.extend(normalized.tolist())
    return vectors


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


KNOWN = {"zh", "en"}


def _language(tag: str | None) -> str:
    """只认明确的中/英。MIXED、UNKNOWN 一律不算——
    实测踩过：一条 MIXED 和一条 zh-Hans 被当成"跨语言"，余弦 0.9 以上，
    把 MiniMax 的发布和 DeepSeek 的发布并成了一条。"""
    text = (tag or "").lower()
    if text.startswith("zh"):
        return "zh"
    if text.startswith("en"):
        return "en"
    return "?"


def cross_lingual_match(
    left_vector: Sequence[float],
    left_language: str | None,
    right_vector: Sequence[float],
    right_language: str | None,
    *,
    threshold: float = CROSS_LINGUAL_THRESHOLD,
) -> tuple[bool, float]:
    """只在语言不同时才允许判同。同语言一律交给锚点与 Jaccard。"""
    left, right = _language(left_language), _language(right_language)
    if left == right or left not in KNOWN or right not in KNOWN:
        return False, 0.0
    similarity = cosine(left_vector, right_vector)
    return similarity >= threshold, round(similarity, 4)


def embed_items(items: Sequence[dict], *, chars: int = 800) -> dict[str, list[float]]:
    """给一批条目算向量。键是 item_id。"""
    texts = []
    for item in items:
        title = item.get("title") or ""
        body = item.get("text_clean") or item.get("text") or ""
        texts.append(title + "。" + body[:chars])
    vectors = encode(texts)
    return {item["item_id"]: vector for item, vector in zip(items, vectors)}
