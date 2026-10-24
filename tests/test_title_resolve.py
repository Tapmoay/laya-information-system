"""标题兜底：页面标题 → 正文首句 → 列表页链接文字 → 放弃。"""

from __future__ import annotations

from tools.crawl import resolve_title


def test_page_title_wins():
    assert resolve_title("真标题", "正文", "链接") == ("真标题", "PAGE")


def test_body_first_sentence_when_no_title():
    title, source = resolve_title(None, "日期：2026年7月4日\n这篇论文提出了一个用普通散文投毒的方法。后面还有。", "链接")
    assert source == "BODY"
    assert title.startswith("这篇论文提出")


def test_anchor_text_when_body_unusable():
    assert resolve_title(None, "   ", "这是列表页上的链接文字") == ("这是列表页上的链接文字", "ANCHOR")


def test_missing_when_nothing_usable():
    assert resolve_title(None, "", "短") == (None, "MISSING")
