"""正文预处理：去导航、按句截断、长度稳定。"""

from __future__ import annotations

from judgment.text import MAX_TEXT_CHARS, prepare, strip_site_chrome, truncate_at_sentence

NAV = "首页 资讯 智能车 智库 活动 扫码关注 量子位 <img src='x.png'> 标题在这里 正文第一句。正文第二句。"


def test_navigation_prefix_is_removed() -> None:
    cleaned = strip_site_chrome(NAV, "标题在这里")
    assert cleaned.startswith("标题在这里")
    assert "首页" not in cleaned


def test_text_is_cut_at_a_sentence_boundary() -> None:
    body = "第一句。" * 2000
    cut, truncated = truncate_at_sentence(body, 100)
    assert truncated is True
    assert len(cut) <= 100
    assert cut.endswith("。")


def test_short_text_is_untouched() -> None:
    body = "短文本。"
    cut, truncated = truncate_at_sentence(body, 100)
    assert cut == body
    assert truncated is False


def test_boilerplate_before_the_body_is_dropped() -> None:
    raw = "标题在这里" + chr(10) + "主作者 关注 少数派作者" + chr(10) + "微信扫码分享" + chr(10) + "这是一段真正的正文，长度足够长，应当被保留下来，它是文章的导语段落，包含足够多的信息量。" + chr(10) + "第二段正文。"
    result = prepare(raw, "标题在这里")
    assert result["text"].startswith("这是一段真正的正文")
    assert "微信扫码分享" not in result["text"]


def test_prepare_reports_length_and_truncation() -> None:
    result = prepare(NAV, "标题在这里")
    assert result["truncated"] is False
    assert result["length"] == len(result["text"])
    assert result["length"] <= MAX_TEXT_CHARS


def test_long_articles_are_cut_near_the_limit() -> None:
    body = "句子。" * 3000
    result = prepare(body, "", limit=500)
    assert result["truncated"] is True
    assert 250 <= result["length"] <= 500


def test_excerpt_skips_title_repeat_and_metadata():
    from judgment.text import excerpt

    text = "同一篇文章的标题在这里\n2026.10.18 12:20 00:00 / 08:59\n正文第一句话是真正的内容。"
    result = excerpt(text, "同一篇文章的标题在这里")
    assert result.startswith("正文第一句话")
    assert "2026.10.18" not in result


def test_excerpt_truncates_at_sentence_boundary():
    from judgment.text import excerpt

    body = "一句话。" * 200
    result = excerpt(body, "", limit=40)
    assert len(result) <= 41
    assert result.endswith("。")


def test_excerpt_returns_empty_for_empty_input():
    from judgment.text import excerpt

    assert excerpt("", "") == ""
