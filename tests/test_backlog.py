"""候补池：这次没选上的，明天还有一次机会。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from store.backlog import carry, load, merge, prune, save

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def _item(item_id, score, title="标题"):
    return ({"item_id": item_id, "title": title, "source_id": "SRC-A"},
            {"item_id": item_id, "scores": {"primary": {"noul": 0.8}}, "score": score})


def test_carry_keeps_only_above_threshold_and_unselected():
    pairs = [_item("item-1", 0.70), _item("item-2", 0.30), _item("item-3", 0.60)]
    items = [p[0] for p in pairs]
    scores = [p[1] for p in pairs]
    result = carry([], items=items, scores=scores, selected={"item-3"}, threshold=0.40, now=NOW)
    ids = [row["item"]["item_id"] for row in result["rows"]]
    assert ids == ["item-1"]


def test_carry_bumps_attempts_for_existing():
    item, score = _item("item-1", 0.70)
    first = carry([], items=[item], scores=[score], selected=set(), threshold=0.40, now=NOW)
    second = carry(first["rows"], items=[item], scores=[score], selected=set(), threshold=0.40, now=NOW)
    assert second["rows"][0]["attempts"] == 2
    assert second["added"] == 0


def test_expired_entries_are_dropped():
    item, score = _item("item-1", 0.70)
    carried = carry([], items=[item], scores=[score], selected=set(), threshold=0.40, now=NOW)
    later = NOW + timedelta(days=9)
    result = carry(carried["rows"], items=[], scores=[], selected=set(), threshold=0.40, now=later)
    assert result["rows"] == []


def test_merge_revives_backlog_items_not_in_today():
    old_item, old_score = _item("item-old", 0.70, "老条目")
    new_item, new_score = _item("item-new", 0.80, "新条目")
    backlog = carry([], items=[old_item], scores=[old_score], selected=set(), threshold=0.40, now=NOW)["rows"]
    result = merge([new_item], [new_score], backlog, now=NOW + timedelta(days=1))
    ids = {i["item_id"] for i in result["items"]}
    assert ids == {"item-old", "item-new"}
    assert result["revived"] == ["item-old"]
    revived = [i for i in result["items"] if i.get("from_backlog")][0]
    assert revived["backlog_attempts"] == 1


def test_merge_prefers_today_version_of_same_item():
    item, score = _item("item-1", 0.70, "旧标题")
    backlog = carry([], items=[item], scores=[score], selected=set(), threshold=0.40, now=NOW)["rows"]
    today = {**item, "title": "新标题"}
    result = merge([today], [score], backlog, now=NOW + timedelta(days=1))
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "新标题"


def test_prune_respects_ttl():
    rows = [{"item": {"item_id": "x"}, "carried_at": (NOW - timedelta(days=10)).isoformat()}]
    assert prune(rows, now=NOW, ttl_days=7) == []


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "backlog.jsonl"
    item, score = _item("item-1", 0.70)
    rows = carry([], items=[item], scores=[score], selected=set(), threshold=0.40, now=NOW)["rows"]
    save(path, rows)
    assert len(load(path)) == 1

def test_cross_lingual_backlog_item_merges_into_today_item():
    """两天前英文进候补，今天来的是中文那条：item_id 不同，但同一件事。"""
    en_item, en_score = _item("item-en", 0.70, "EU approves AI regulation framework")
    en_item["detected_language"] = "en"
    backlog = carry([], items=[en_item], scores=[en_score], selected=set(), threshold=0.40, now=NOW)["rows"]

    zh_item, zh_score = _item("item-zh", 0.66, "欧盟批准人工智能监管框架")
    zh_item["detected_language"] = "zh-Hans"

    def same_event(left, right):
        return {"same": left.get("item_id") == "item-en" and right.get("item_id") == "item-zh", "similarity": 0.96}

    result = merge([zh_item], [zh_score], backlog, now=NOW + timedelta(days=1), same_event=same_event)
    ids = [i["item_id"] for i in result["items"]]
    assert ids == ["item-zh"]
    merged = result["items"][0]
    assert merged["backlog_merged_from"] == ["item-en"]
    assert merged["backlog_attempts"] >= 1
    assert result["revived"] == []


def test_different_language_but_different_event_still_revives():
    en_item, en_score = _item("item-en", 0.70, "Sewell Killingstad on art")
    en_item["detected_language"] = "en"
    backlog = carry([], items=[en_item], scores=[en_score], selected=set(), threshold=0.40, now=NOW)["rows"]
    zh_item, zh_score = _item("item-zh", 0.66, "地标大屏为什么有的刷屏")
    zh_item["detected_language"] = "zh-Hans"

    result = merge([zh_item], [zh_score], backlog, now=NOW + timedelta(days=1), same_event=lambda a, b: {"same": False, "similarity": 0.5})
    assert [i["item_id"] for i in result["items"]] == ["item-zh", "item-en"]
    assert result["revived"] == ["item-en"]
