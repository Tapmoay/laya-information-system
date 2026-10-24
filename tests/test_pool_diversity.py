"""候选池的多样性：一个源最多两席，示例第二方向至少三个不同源各一席。"""

from __future__ import annotations

from tools.agent_select import _cap_per_source, _visual_candidates


def _row(item_id, source, field="ai", score=0.6):
    return {
        "item_id": item_id,
        "score": score,
        "field": field,
        "hard_drop": None,
        "provenance": {"source_id": source},
    }


def test_cap_per_source_keeps_at_most_two():
    rows = [_row("a1", "S1"), _row("a2", "S1"), _row("a3", "S1"), _row("b1", "S2")]
    kept = _cap_per_source(rows, 2)
    assert [r["item_id"] for r in kept] == ["a1", "a2", "b1"]


def test_visual_candidates_pick_distinct_sources():
    rows = [
        _row("g1", "GOOOD", "architecture"),
        _row("g2", "GOOOD", "architecture"),
        _row("h1", "HYPER", "art"),
        _row("d1", "ADQUAN", "design"),
        _row("x1", "QBITAI", "ai"),
    ]
    picked = _visual_candidates(rows, exclude=set(), limit=3)
    assert [r["item_id"] for r in picked] == ["g1", "h1", "d1"]


def test_visual_candidates_skip_hard_drop_and_excluded():
    rows = [_row("g1", "GOOOD", "architecture"), _row("h1", "HYPER", "art")]
    rows[0]["hard_drop"] = "MARKETING"
    picked = _visual_candidates(rows, exclude=set(), limit=3)
    assert [r["item_id"] for r in picked] == ["h1"]
    assert _visual_candidates(rows, exclude={"h1"}, limit=3) == []
