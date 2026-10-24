"""单语近似重复：整段照抄靠这个抓，跨语言靠 embedding（还没有）。"""

from __future__ import annotations

from judgment.simhash import fingerprint, hamming, jaccard, near_duplicate

# 用有变化的正文，不要用重复同一句：重复句子会让 shingle 集合小到失真，
# 那样测出来的不是算法行为，是 fixture 的行为。
TEXT = "".join(
    "第{}段：某公司今天发布新模型，参数量提升到 {} 亿，API 价格不变，上下文窗口扩到 {}K。".format(
        index, 3000 + index * 7, 200 + index
    )
    for index in range(40)
)


def test_identical_text_has_full_overlap():
    assert jaccard(TEXT, TEXT) == 1.0
    assert hamming(fingerprint(TEXT), fingerprint(TEXT)) == 0


def test_editor_note_does_not_break_detection():
    copied = "编者按：本文转自某处，有删改。" + TEXT
    assert jaccard(TEXT, copied) >= 0.9


def test_trimmed_copy_is_still_caught():
    trimmed = TEXT[: int(len(TEXT) * 0.8)]
    assert jaccard(TEXT, trimmed) >= 0.7


def test_unrelated_articles_score_near_zero():
    other = "上海大歌剧院正式揭开帷幕，设计方为 Snøhetta，外墙采用玻璃幕墙。" * 20
    assert jaccard(TEXT, other) < 0.1


def test_fingerprint_is_stable():
    assert fingerprint(TEXT) == fingerprint(TEXT)


def test_empty_text_never_matches():
    assert near_duplicate(fingerprint(""), fingerprint(TEXT)) is False
