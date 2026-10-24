"""已推送台账：7 天内一律不推，7 天以后也要有新进展才推。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from store.repeat_guard import check, entry_for, load, partition, remember

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def _prior(item_id="item-1", days=1, score=0.60, anchors=("deepseek", "4.1")):
    return {
        "item_id": item_id,
        "canonical_url": "https://example.com/a",
        "event_key": "ev-abc",
        "event_anchors": list(anchors),
        "score": score,
        "delivered_at": (NOW - timedelta(days=days)).isoformat(timespec="seconds"),
    }


def _item(item_id="item-1", score=0.60, anchors=("deepseek", "4.1")):
    return {
        "item_id": item_id,
        "canonical_url": "https://example.com/a",
        "event_key": "ev-abc",
        "event_anchors": list(anchors),
        "score": score,
    }


def test_unseen_item_is_pushed():
    verdict = check(_item(), [], now=NOW)
    assert verdict["decision"] == "PUSH" and verdict["reason"] == "NEW"


def test_repeat_within_window_is_blocked():
    verdict = check(_item(), [_prior(days=2)], now=NOW)
    assert verdict["decision"] == "BLOCK" and verdict["reason"] == "REPEAT_WINDOW"


def test_same_event_from_another_outlet_is_blocked_within_window():
    item = {**_item(item_id="item-2"), "canonical_url": "https://other.com/b"}
    verdict = check(item, [_prior(days=3)], now=NOW)
    assert verdict["decision"] == "BLOCK" and verdict["match"] == "SAME_EVENT"


def test_after_window_without_progress_stays_blocked():
    """这正是用户担心的那种情况：没价值的东西过了 7 天又被推一次。"""
    verdict = check(_item(score=0.60), [_prior(days=10, score=0.60)], now=NOW)
    assert verdict["decision"] == "BLOCK" and verdict["reason"] == "NO_PROGRESS"


def test_after_window_high_score_but_no_new_anchor_stays_blocked():
    verdict = check(_item(score=0.80), [_prior(days=10, score=0.60)], now=NOW)
    assert verdict["decision"] == "BLOCK" and verdict["reason"] == "NO_PROGRESS"


def test_after_window_new_anchor_but_no_score_gain_stays_blocked():
    verdict = check(_item(score=0.60, anchors=("deepseek", "4.1", "5.0")), [_prior(days=10, score=0.60)], now=NOW)
    assert verdict["decision"] == "BLOCK" and verdict["reason"] == "NO_PROGRESS"


def test_after_window_high_score_and_new_anchor_is_repushed():
    verdict = check(_item(score=0.85, anchors=("deepseek", "5.0")), [_prior(days=10, score=0.60)], now=NOW)
    assert verdict["decision"] == "PUSH" and verdict["reason"] == "REPUSH_UPDATED"
    assert verdict["new_anchors"] == ["5.0"]


def test_partition_splits_allowed_and_blocked():
    fresh = {**_item(item_id="item-9"), "event_key": "ev-new", "canonical_url": "https://example.com/z"}
    items = [_item(item_id="item-1"), fresh]
    result = partition(items, [_prior(days=1)], now=NOW)
    assert [row["item_id"] for row in result["allowed"]] == ["item-9"]
    assert result["blocked"][0]["reason"] == "REPEAT_WINDOW"


def test_ledger_round_trip(tmp_path):
    path = tmp_path / "delivered.jsonl"
    remember(path, [entry_for(_item(), now=NOW)])
    rows = load(path)
    assert len(rows) == 1 and rows[0]["item_id"] == "item-1"
    verdict = check(_item(), rows, now=NOW)
    assert verdict["decision"] == "BLOCK"
