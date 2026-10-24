"""事实库：先用便宜比对排掉重复，再把真正需要还原的条目送去跑模型。"""

from __future__ import annotations

import json

from store.fact_library import IDENTICAL, SIMILAR, append, entry_for, load, lookup

ENTRY = {
    "item_id": "item-1",
    "title": "DeepSeek 发布 V4.1-Flash-1017",
    "claims": [
        {"subject": "DeepSeek", "relation": "发布", "object": "V4.1-Flash-1017"},
        {"subject": "DeepSeek", "relation": "保持", "object": "API 价格不变"},
    ],
    "stored_at": "2026-10-19T00:00:00+00:00",
}


def test_same_statement_is_identical():
    item = {"title": "DeepSeek 发布 V4.1-Flash-1017，API 价格不变", "text_clean": ""}
    verdict = lookup(item, [ENTRY])
    assert verdict["verdict"] == "IDENTICAL" and verdict["ratio"] >= IDENTICAL


def test_partial_overlap_is_treated_as_update():
    """只覆盖一部分 → 可能是后续更新，放行但要留意，不能直接排除。"""
    item = {"title": "DeepSeek V4.1-Flash-1017 实测：价格没变，速度更快了", "text_clean": ""}
    verdict = lookup(item, [ENTRY])
    assert verdict["verdict"] in {"SIMILAR", "IDENTICAL"}


def test_unrelated_item_is_new():
    item = {"title": "Anthropic 更新独立评估器说明", "text_clean": ""}
    assert lookup(item, [ENTRY])["verdict"] == "NEW"


def test_empty_library_never_matches():
    assert lookup({"title": "任何东西"}, [])["verdict"] == "NEW"


def test_library_round_trip(tmp_path):
    path = tmp_path / "fact-library.jsonl"
    append(path, [entry_for({"item_id": "item-1", "title": "t", "canonical_url": "u"}, ENTRY["claims"], now="2026-10-19T00:00:00+00:00")])
    rows = load(path)
    assert len(rows) == 1 and rows[0]["claims"][0]["subject"] == "DeepSeek"


def test_broken_line_is_skipped(tmp_path):
    path = tmp_path / "fact-library.jsonl"
    path.write_text('{"item_id": "a"}\nnot json\n', encoding="utf-8")
    assert len(load(path)) == 1
