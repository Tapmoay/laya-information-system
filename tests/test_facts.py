"""事实抽取与按事实去重：比的是"说了什么"，不是"标题长什么样"。"""

from __future__ import annotations

from judgment.facts import Claim, cluster, fingerprint, overlap, parse


def _claims(*triples) -> list[Claim]:
    return [Claim(*triple) for triple in triples]


def test_parse_reads_lines_and_drops_malformed():
    raw = "DeepSeek|发布|V4.1-Flash-1017|2026-10-17\n这行没有分隔符\n|||\nOpenAI|发布|GPT-6|-"
    claims = parse(raw)
    assert len(claims) == 2
    assert claims[0].subject == "DeepSeek" and claims[0].object == "V4.1-Flash-1017"


def test_parse_returns_empty_for_none():
    assert parse("NONE") == []


def test_parse_deduplicates_same_fact():
    raw = "A|发布|B|-\nA|发布|B|-\n"
    assert len(parse(raw)) == 1


def test_parse_caps_number_of_claims():
    raw = "\n".join("s{}|r|x{}".format(i, i) for i in range(20))
    assert len(parse(raw)) == 6


def test_fact_key_ignores_case_punctuation_and_time():
    left = Claim("DeepSeek", "发布", "V4.1-Flash-1017", "2026-10-17")
    right = Claim("deepseek", "发布。", "V4.1-Flash-1017", "-")
    assert left.key == right.key


def test_overlap_single_fact_matches_long_report():
    """短消息只讲一件事，长报道讲五件事，只要那件事对上了就是同一件事。"""
    short = _claims(("deepseek", "发布", "v4.1"))
    long = _claims(
        ("deepseek", "发布", "v4.1"),
        ("deepseek", "提升", "hle 42.6"),
        ("deepseek", "保持", "api 价格"),
    )
    ok, info = overlap(short, long)
    assert ok and info["shared"] == 1


def test_overlap_requires_two_when_both_are_multi_claim():
    left = _claims(("a", "b", "c"), ("d", "e", "f"))
    right = _claims(("a", "b", "c"), ("g", "h", "i"))
    ok, info = overlap(left, right)
    assert not ok and info["need"] == 2


def test_no_facts_never_matches():
    ok, info = overlap([], [Claim("a", "b", "c")])
    assert not ok and info["reason"] == "NO_FACTS"


def test_cluster_keeps_series_episodes_apart():
    """同源连载：不同期讲的是不同的事，按事实就该分开。"""
    items = [
        {"item_id": "item-1", "claims": _claims(("随机化", "是", "第 61 期主题"))},
        {"item_id": "item-2", "claims": _claims(("烂耳朵", "是", "第 60 期主题"))},
    ]
    mapping = cluster(items)
    assert mapping["item-1"]["fact_cluster_id"] != mapping["item-2"]["fact_cluster_id"]


def test_cluster_merges_same_fact_from_two_outlets():
    items = [
        {"item_id": "item-1", "claims": _claims(("deepseek", "发布", "v4.1"))},
        {"item_id": "item-2", "claims": _claims(("deepseek", "发布", "v4.1"), ("deepseek", "保持", "价格"))},
    ]
    mapping = cluster(items)
    assert mapping["item-1"]["fact_cluster_id"] == mapping["item-2"]["fact_cluster_id"]


def test_cluster_does_not_chain_through_a_shared_middle():
    """A≈B、B≈C 不能自动推出 A≈C。这正是"约等于"最糟糕的后果。"""
    items = [
        {"item_id": "item-a", "claims": _claims(("x", "r1", "y"), ("x", "r2", "z"))},
        {"item_id": "item-b", "claims": _claims(("x", "r1", "y"), ("x", "r2", "z"))},
        {"item_id": "item-c", "claims": _claims(("x", "r2", "z"), ("x", "r3", "w"))},
    ]
    mapping = cluster(items)
    assert mapping["item-a"]["fact_cluster_id"] == mapping["item-b"]["fact_cluster_id"]
    assert mapping["item-c"]["fact_cluster_id"] != mapping["item-a"]["fact_cluster_id"]


def test_cluster_is_deterministic():
    items = [
        {"item_id": "item-b", "claims": _claims(("deepseek", "发布", "v4.1"))},
        {"item_id": "item-a", "claims": _claims(("deepseek", "发布", "v4.1"))},
    ]
    assert cluster(items) == cluster(items)


def test_fingerprint_is_order_independent():
    left = _claims(("a", "b", "c"), ("d", "e", "f"))
    right = list(reversed(left))
    assert fingerprint(left) == fingerprint(right)
