"""终选者写的摘要优先于机械摘要。"""

from __future__ import annotations

from tools.final_digest import _entry


ITEM = {
    "item_id": "item-1",
    "title": "某篇文章",
    "text": "这是一段足够长的正文，用来证明机械摘要仍然能产出内容，而不是被页头过滤成空字符串。",
    "canonical_url": "https://example.com/a",
    "source_name": "Example",
}


def test_agent_gist_wins_over_mechanical_summary():
    entry = _entry(ITEM, {"reason": "为什么", "gist": "这篇发现了跨账号泄漏。"}, None)
    assert entry["digest"] == "这篇发现了跨账号泄漏。"
    assert entry["digest_is_translation"] is True


def test_blank_gist_falls_back_to_mechanical_summary():
    entry = _entry(ITEM, {"reason": "为什么", "gist": "   "}, None)
    assert entry["digest"] != ""
    assert entry["digest_is_translation"] is False
