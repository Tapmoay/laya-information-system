"""SimHash：单语近似重复的指纹。

用途很具体：**同一件事被同一语言的不同媒体改写转载**（改标题、删段落、换配图）。
这类重复在中文科技/设计媒体里最常见，而它不需要语义理解——字面就高度重合。

和事件锚点的分工：
- 锚点（judgment/events.py）靠专有名词与版本号，能跨语言、能识别"同一件事的不同角度"，
  但对"整段照抄只改开头"不敏感。
- SimHash 靠整体词形分布，对"整段照抄"极敏感，但换个语言就完全失效。

两者互补，所以是**加一条规则**，不是替代。

算法（Charikar 2002；Manku 等 2007 在网页抓取里的做法）：
把文本切成 shingle → 每个 shingle 哈希成 64 位 → 逐位加权投票 → 得到 64 位指纹。
两个指纹的汉明距离越小，原文越像。网页去重的经验阈值是 ≤3。

为什么不用 MinHash+LSH：那个在超大语料下才有优势（要建桶、要调 band 数）。
我们一次运行几十到几百条，直接两两比汉明距离就够，省掉一整层复杂度。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

BITS = 64
CJK_RUN = re.compile("[\u4e00-\u9fff]+")
WORD = re.compile(r"[a-z0-9]+")
# 网页去重的经验阈值：汉明距离 ≤3 视为近似重复（Manku 等 2007）
NEAR_DUPLICATE_DISTANCE = 3


def shingles(text: str, *, size: int = 3) -> list[str]:
    """CJK 用连续 size 字，拉丁用连续 size 词。混排时两者都出。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    out: list[str] = []
    for run in CJK_RUN.findall(text):
        for index in range(max(1, len(run) - size + 1)):
            piece = run[index : index + size]
            if len(piece) == size:
                out.append(piece)
    words = WORD.findall(text)
    for index in range(max(1, len(words) - size + 1)):
        piece = " ".join(words[index : index + size])
        if piece.strip():
            out.append(piece)
    return out


def _hash64(piece: str) -> int:
    digest = hashlib.blake2b(piece.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def fingerprint(text: str, *, size: int = 3) -> int:
    """返回 64 位指纹。文本太短（没有 shingle）时返回 0，表示"算不出来"。"""
    pieces = shingles(text, size=size)
    if not pieces:
        return 0
    counts: dict[str, int] = {}
    for piece in pieces:
        counts[piece] = counts.get(piece, 0) + 1
    vector = [0] * BITS
    for piece, weight in counts.items():
        value = _hash64(piece)
        for bit in range(BITS):
            if value >> bit & 1:
                vector[bit] += weight
            else:
                vector[bit] -= weight
    result = 0
    for bit in range(BITS):
        if vector[bit] > 0:
            result |= 1 << bit
    return result


def shingle_set(text: str, *, size: int = 3) -> set[str]:
    return set(shingles(text, size=size))


def jaccard_sets(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def jaccard(left: str, right: str, *, size: int = 3) -> float:
    """shingle 集合的精确 Jaccard。

    几十到几百条这个量级，直接算精确值比 SimHash 更好：
    SimHash 的意义是**避免两两比较**，而两两比较在我们这里根本不贵
    （200 条约 2 万对，一秒内算完）。近似指纹反而会因为文档短而失真——
    实测把一篇 2000 字的文章删掉 20%，SimHash 汉明距离就从 0 跳到 11，
    远超网页去重常用的 ≤3。
    """
    a = set(shingles(left, size=size))
    b = set(shingles(right, size=size))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def near_duplicate(left: int, right: int, *, max_distance: int = NEAR_DUPLICATE_DISTANCE) -> bool:
    if not left or not right:
        return False
    return hamming(left, right) <= max_distance
