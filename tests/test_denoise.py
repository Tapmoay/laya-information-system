"""跨页去导航：清单要稳定，削得要保守。"""

from __future__ import annotations

from judgment.denoise import apply, chrome_lines, fingerprint, shrink_ratio, strip_chrome

NAV = "登陆账号\n站内信\n厂商大全"


def _pages(count: int, body_prefix: str = "正文") -> list[str]:
    return [NAV + "\n" + body_prefix + str(index) + " 这是一段真正的内容。" for index in range(count)]


def test_lines_on_most_pages_are_chrome():
    chrome = chrome_lines(_pages(6), min_pages=4)
    assert "登陆账号" in chrome
    assert "站内信" in chrome
    assert not any("真正的内容" in line for line in chrome)


def test_too_few_pages_yields_no_chrome():
    assert chrome_lines(_pages(3), min_pages=4) == []


def test_strip_removes_only_chrome():
    chrome = chrome_lines(_pages(6), min_pages=4)
    text = _pages(1)[0]
    cleaned = strip_chrome(text, chrome)
    assert "登陆账号" not in cleaned
    assert "真正的内容" in cleaned


def test_fingerprint_normalises_whitespace_and_case():
    assert fingerprint("  登陆账号 ") == fingerprint("登陆账号")
    assert fingerprint("Log In") == fingerprint("log  in")


def test_apply_reports_shrink():
    outcome = apply(_pages(6))
    assert len(outcome["cleaned"]) == 6
    assert all(value > 0 for value in outcome["shrink"])


def test_shrink_ratio_is_bounded():
    assert shrink_ratio("abcd", "abcd") == 0.0
    assert 0.0 < shrink_ratio("a" * 100, "a" * 25) < 1.0
    assert shrink_ratio("", "") == 0.0


def test_single_page_content_is_untouched_when_no_chrome():
    text = "只有一段内容"
    assert strip_chrome(text, []) == text
