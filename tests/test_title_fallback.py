"""缺标题时从正文推一个，而不是把整条丢掉。"""

from __future__ import annotations

from ingest.html_extract import derive_title


def test_skips_header_lines_and_takes_first_sentence():
    text = "日期：2026年7月4日 | 预计阅读时间：31分钟\n作者：某某\n这篇论文提出了一个用普通散文投毒的方法。后面是正文。"
    assert derive_title(text) == "这篇论文提出了一个用普通散文投毒的方法"


def test_returns_none_when_nothing_usable():
    assert derive_title("") is None
    assert derive_title("短\n日期：2026年") is None


def test_long_line_is_clipped():
    title = derive_title("一" * 200)
    assert title is not None
    assert len(title) <= 60
    assert title.endswith("…")
