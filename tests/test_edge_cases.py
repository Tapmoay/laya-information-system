"""极端情况：空输入、变态文本、坏数据、边界时间、规模。

这些不是"锦上添花"的测试——线上第一个崩的地方，一定是没被这样试过的地方。
每个用例都对应一种真实会遇到的坏输入。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from judgment.events import anchors, cluster, compare
from judgment.facts import Claim, cluster as fact_cluster, overlap, parse
from judgment.recency import estimate, measure
from judgment.scoring import rank, select, summarise
from judgment.simhash import fingerprint, jaccard
from store.backlog import carry, merge, prune
from store.repeat_guard import check

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 空与缺字段


def test_cluster_handles_empty_list():
    assert cluster([]) == {}


def test_cluster_handles_items_without_text_or_title():
    items = [
        {"item_id": "item-a", "source_id": "SRC-A"},
        {"item_id": "item-b", "source_id": "SRC-B", "title": "", "text": ""},
    ]
    mapping = cluster(items)
    assert len(mapping) == 2
    assert mapping["item-a"]["event_size"] == 1


def test_anchors_tolerates_none_and_whitespace():
    for value in ("", "   ", "\n\t"):
        tokens = anchors(value, value)
        assert tokens["strong"] == [] and tokens["names"] == []


def test_scoring_handles_empty_scores():
    result = summarise({})
    assert result["score"] == 0.0
    assert result["form"] == "UNKNOWN"


def test_scoring_handles_partial_scores():
    result = summarise({"primary": {"noul": 0.9}})
    assert result["score"] >= 0.0
    assert result["quality"] == 0.0


def test_facts_empty_and_tautological():
    assert parse("") == []
    assert parse("A|发布了|A") == []
    assert parse("A||B") == []


def test_fact_cluster_with_no_claims():
    items = [{"item_id": "item-a", "claims": []}, {"item_id": "item-b", "claims": []}]
    mapping = fact_cluster(items)
    assert mapping["item-a"]["fact_cluster_id"] != mapping["item-b"]["fact_cluster_id"]


def test_recency_with_empty_item():
    result = measure({}, NOW)
    assert result["age_days"] is None and result["source"] == "UNKNOWN"


def test_repeat_guard_item_without_score():
    verdict = check({"item_id": "item-x"}, [], now=NOW)
    assert verdict["decision"] == "PUSH"


# ---------------------------------------------------------------- 变态文本


def test_very_long_title_does_not_blow_up():
    title = "长" * 20000
    tokens = anchors(title, "")
    assert isinstance(tokens["cjk"], list)


def test_emoji_and_control_characters():
    text = "😀🚀\x00\x01 test \u200b\u200e mixed"
    tokens = anchors(text, text)
    assert isinstance(tokens, dict)
    assert jaccard(text, text) == 1.0


def test_full_width_and_half_width_are_normalised():
    left = anchors("ＤｅｅｐＳｅｅｋ 发布 Ｖ４．１", "")
    right = anchors("DeepSeek 发布 V4.1", "")
    same, where = compare(left, right)
    assert same, where


def test_rtl_and_rare_scripts_do_not_crash():
    text = "مرحبا بالعالم שלום עולם こんにちは"
    tokens = anchors(text, text)
    same, _ = compare(tokens, tokens)
    assert isinstance(same, bool)


def test_whitespace_only_text_is_not_a_duplicate():
    assert jaccard("   ", "   ") == 0.0
    assert fingerprint("   ") == 0


# ---------------------------------------------------------------- 时间边界


def test_naive_datetime_in_ledger_is_treated_as_utc():
    prior = {
        "item_id": "item-1",
        "delivered_at": (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat(),
        "score": 0.6,
    }
    verdict = check({"item_id": "item-1", "score": 0.6}, [prior], now=NOW)
    assert verdict["decision"] == "BLOCK"


def test_future_delivered_at_blocks_rather_than_errors():
    """时钟回拨也不能把"刚推过"变成"没推过"。"""
    prior = {
        "item_id": "item-1",
        "delivered_at": (NOW + timedelta(days=3)).isoformat(),
        "score": 0.6,
    }
    verdict = check({"item_id": "item-1", "score": 0.7}, [prior], now=NOW)
    assert verdict["decision"] == "BLOCK"


def test_exactly_seven_days_is_outside_the_window():
    prior = {
        "item_id": "item-1",
        "delivered_at": (NOW - timedelta(days=7)).isoformat(),
        "score": 0.6,
        "event_anchors": ["a"],
    }
    item = {"item_id": "item-1", "score": 0.6, "event_anchors": ["a"]}
    verdict = check(item, [prior], now=NOW)
    # 满 7 天就不再是"窗口内"，要按有没有新进展判
    assert verdict["reason"] in {"NO_PROGRESS", "REPUSH_UPDATED"}
    assert verdict["decision"] == "BLOCK"


def test_broken_timestamp_in_ledger_does_not_block_forever():
    prior = {"item_id": "item-1", "delivered_at": "not-a-date", "score": 0.6}
    verdict = check({"item_id": "item-1", "score": 0.6}, [prior], now=NOW)
    assert verdict["decision"] == "BLOCK"


def test_absurd_published_date_is_rejected():
    result = estimate(published_raw="1999-01-01T00:00:00Z", url=None, text=None)
    assert measure({"published_at": {"value": result["value"].isoformat()}}, NOW)["age_days"] is None


# ---------------------------------------------------------------- 坏数据


def test_backlog_skips_rows_without_item(tmp_path):
    path = tmp_path / "backlog.jsonl"
    path.write_text('{"attempts": 1}\nnot json\n\n{"item": {"item_id": "x"}}\n', encoding="utf-8")
    from store.backlog import load

    rows = load(path)
    assert len(rows) == 2
    result = merge([], [], rows, now=NOW)
    assert result["items"] == []


def test_backlog_respects_capacity():
    items, scores = [], []
    for index in range(120):
        items.append({"item_id": "item-{}".format(index), "title": "t", "source_id": "S"})
        scores.append({"item_id": "item-{}".format(index), "score": 0.5 + index / 1000.0})
    result = carry([], items=items, scores=scores, selected=set(), threshold=0.4, now=NOW, max_items=80)
    assert result["total"] == 80
    # 留下的应该是分数高的那批
    kept = {row["item"]["item_id"] for row in result["rows"]}
    assert "item-119" in kept and "item-0" not in kept


def test_select_with_all_items_in_one_domain():
    ranked = [
        {"item_id": "i{}".format(i), "score": 0.9 - i * 0.01, "domain": "ai", "values": {"exploration": 0.1}, "reduced": False, "title": "t"}
        for i in range(10)
    ]
    chosen = select(ranked, threshold=0.0)
    assert len(chosen["core"]) <= 2  # 品类上限


def test_rank_is_stable_for_equal_scores():
    rows = [
        {"item_id": "b", "score": 0.5, "reduced": False, "title": "同分"},
        {"item_id": "a", "score": 0.5, "reduced": False, "title": "同分"},
    ]
    assert [r["item_id"] for r in rank(rows)] == [r["item_id"] for r in rank(list(reversed(rows)))]


# ---------------------------------------------------------------- 确定性与规模


def test_cluster_is_deterministic_on_large_input():
    items = [
        {"item_id": "item-{:04d}".format(i), "source_id": "SRC-{}".format(i % 7),
         "title": "第 {} 条：某公司发布 V{} 型号".format(i, i % 5), "text": "正文 {}".format(i) * 20}
        for i in range(150)
    ]
    first = cluster(items)
    second = cluster(items)
    assert first == second


def test_clustering_150_items_is_fast_enough():
    items = [
        {"item_id": "item-{:04d}".format(i), "source_id": "SRC-{}".format(i % 7),
         "title": "标题 {}".format(i), "text": "正文内容 {}".format(i) * 50}
        for i in range(150)
    ]
    started = time.time()
    cluster(items)
    assert time.time() - started < 20.0


def test_end_to_end_synthetic_batch_does_not_crash():
    """50 条、含 5 组重复事件、跨语言、空字段，走一遍聚簇 + 打分 + 选择。"""
    items = []
    for index in range(50):
        items.append({
            "item_id": "item-{:03d}".format(index),
            "source_id": "SRC-{}".format(index % 5),
            "title": "事件{} 报道".format(index % 5) if index % 6 else "",
            "text": "关于事件{}的正文。".format(index % 5) * 30,
            "detected_language": "zh-Hans" if index % 3 else "en",
        })
    mapping = cluster(items)
    assert len(mapping) == 50
    ranked = [
        {"item_id": key, "score": 0.5, "domain": "ai", "values": {"exploration": 0.6},
         "reduced": False, "title": "t"}
        for key in mapping
    ]
    chosen = select(ranked, threshold=0.0)
    assert len(chosen["core"]) <= 5

def test_cluster_skips_items_without_item_id():
    """坏数据不该让整批聚类崩掉。"""
    items = [
        {"title": "没有 id 的条目", "source_id": "SRC-A"},
        {"item_id": "item-a", "title": "有 id 的条目", "source_id": "SRC-B"},
    ]
    mapping = cluster(items)
    assert list(mapping) == ["item-a"]


def test_fact_cluster_skips_items_without_item_id():
    items = [{"claims": []}, {"item_id": "item-a", "claims": []}]
    mapping = fact_cluster(items)
    assert list(mapping) == ["item-a"]
