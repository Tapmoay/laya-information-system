"""打分与 5+2 选择的规则测试（不需要模型）。"""

from __future__ import annotations

from judgment.scoring import DEFAULT_WEIGHTS, Weights, rank, select, summarise


def scores(value: float, quality: float, form: str = "TECHNICAL", recruit: float = 0.0) -> dict:
    return {
        "primary": {"noul": value * 0.9},
        "value_task": {"noul": value},
        "value_exploration": {"noul": value * 0.5},
        "quality_testable": {"noul": quality},
        "quality_increment": {"noul": quality},
        "form": {"probabilities": {form: 0.8, "PRODUCT": 0.2}},
        "recruit": {"noul": recruit},
    }


def test_value_is_the_max_of_any_single_criterion() -> None:
    result = summarise(scores(0.9, 0.2))
    assert result["value_max"] == 0.9, "价值只取政策五项里最高的一项"
    assert result["policy_max"] == 0.9
    assert round(result["direction_max"], 2) == 0.81, "方向槽只用来解释服务哪个方向"


def test_quality_is_the_average_of_the_two_traits() -> None:
    result = summarise(scores(0.5, 0.6))
    assert result["quality"] == 0.6


def test_marketing_is_reduced_not_discarded() -> None:
    result = summarise(scores(0.8, 0.8, form="MARKETING"))
    assert result["reduced"] is True
    assert result["score"] > 0, "降权不是清零"
    assert result["score"] < summarise(scores(0.8, 0.8))["score"]


def test_recruit_question_can_flag_a_product_form() -> None:
    result = summarise(scores(0.6, 0.6, form="PRODUCT", recruit=0.7))
    assert result["reduced"] is True, "形态判成 PRODUCT 时，招募信号仍能触发降权"


def test_quality_beats_value_for_noise() -> None:
    real = summarise(scores(0.65, 0.82))
    teaser = summarise(scores(0.80, 0.50, form="EVENT"))
    assert real["score"] > teaser["score"], "高价值但低质量的预告不应排在技术文前面"


def test_select_keeps_five_core_plus_two_exploration() -> None:
    items = [
        {"title": f"t{i}", "score": 1.0 - i * 0.05, "reduced": False,
         "values": {"exploration": 0.9 if i in (7, 8) else 0.05}}
        for i in range(9)
    ]
    picked = select(rank(items), core=5, exploration=2)
    assert len(picked["core"]) == 5
    assert [item["title"] for item in picked["exploration"]] == ["t7", "t8"]
    assert picked["rest"]


def test_reduced_items_do_not_take_exploration_slots() -> None:
    items = [
        {"title": "core-a", "score": 0.9, "reduced": False, "values": {}},
        {"title": "core-b", "score": 0.8, "reduced": False, "values": {}},
        {"title": "good", "score": 0.7, "reduced": False, "values": {"exploration": 0.8}},
        {"title": "marketing", "score": 0.69, "reduced": True, "values": {"exploration": 0.95}},
    ]
    picked = select(rank(items), core=2, exploration=1)
    assert [item["title"] for item in picked["exploration"]] == ["good"]


def test_exploration_slots_stay_empty_below_threshold() -> None:
    items = [
        {"title": "core-a", "score": 0.9, "reduced": False, "values": {}},
        {"title": "weak", "score": 0.7, "reduced": False, "values": {"exploration": 0.3}},
    ]
    picked = select(rank(items), core=1, exploration=2, exploration_threshold=0.5)
    assert picked["exploration"] == [], "不够格就空着，政策允许少推荐"


def test_overrides_are_recorded_with_reasons() -> None:
    items = [
        {"title": "a", "item_id": "a", "score": 0.9, "reduced": False, "values": {}},
        {"title": "b", "item_id": "b", "score": 0.8, "reduced": False, "values": {}},
    ]
    picked = select(
        rank(items), core=1, exploration=0,
        overrides={"b": {"include": True, "reason": "方向相关度最高"}},
    )
    chosen = [item["title"] for item in picked["core"] + picked["exploration"]]
    assert "b" in chosen, "被覆盖的条目必须进入结果"
    assert picked["overrides"][0]["reason"] == "方向相关度最高"


def test_reduced_items_sort_last_on_ties() -> None:
    items = [
        {"title": "a", "score": 0.7, "reduced": True, "values": {}},
        {"title": "b", "score": 0.7, "reduced": False, "values": {}},
    ]
    assert [item["title"] for item in rank(items)] == ["b", "a"]


def test_weights_are_configurable() -> None:
    strict = Weights(value=0.3, quality=0.7, reduced=0.3)
    loose = Weights(value=0.3, quality=0.7, reduced=0.05)
    sample = scores(0.8, 0.8, form="MARKETING")
    assert summarise(sample, strict)["score"] < summarise(sample, loose)["score"]


def test_summary_reports_relatedness():
    scores = {
        "primary": {"noul": 0.1}, "secondary": {"noul": 0.6}, "exploration": {"noul": 0.7},
        "task": {"noul": 0.2},
        "value_task": {"noul": 0.5}, "value_capability": {"noul": 0.5}, "value_decision": {"noul": 0.5},
        "value_exploration": {"noul": 0.8}, "value_environment": {"noul": 0.5},
        "quality_testable": {"noul": 0.6}, "quality_increment": {"noul": 0.6},
        "form": {"probabilities": {"TECHNICAL": 0.9}}, "recruit": {"noul": 0.1},
    }
    assert summarise(scores)["relatedness"] == 0.6


def test_exploration_prefers_related_items_over_more_alien_ones():
    """同样陌生，更贴近本领域的先上。"""
    def item(name, exploration, relatedness, score):
        return {
            "item_id": name, "title": name, "score": score, "reduced": False,
            "values": {"exploration": exploration}, "relatedness": relatedness,
        }

    ranked = rank([
        item("alien", 0.9, 0.0, 0.70),
        item("bridge", 0.6, 0.5, 0.80),
        item("core", 0.95, 0.9, 0.90),
    ])
    picked = select(ranked, core=1, exploration=2)
    assert [row["item_id"] for row in picked["exploration"]] == ["bridge", "alien"]


def test_core_prefers_primary_and_caps_one_domain():
    def item(name, score, domain, values, exploration=0.1):
        return {"item_id": name, "title": name, "score": score, "reduced": False,
                "domain": domain, "values": {"exploration": exploration, **values}}

    ranked = rank([
        item("arch1", 0.90, "architecture", {"primary": 0.05, "secondary": 0.80, "task": 0.05}),
        item("arch2", 0.88, "architecture", {"primary": 0.05, "secondary": 0.78, "task": 0.05}),
        item("arch3", 0.86, "architecture", {"primary": 0.05, "secondary": 0.76, "task": 0.05}),
        item("ai1", 0.60, "technology", {"primary": 0.70, "secondary": 0.05, "task": 0.20}),
    ])
    picked = select(ranked, core=3, exploration=0)
    ids = [row["item_id"] for row in picked["core"]]
    # 主目标候选优先进入，且建筑类最多占 2 席
    assert ids[0] == "ai1"
    assert sum(1 for name in ids if name.startswith("arch")) == 2
    assert "arch3" not in ids


def test_domain_cap_is_configurable():
    def item(name, score, domain):
        # 用主方向内容测品类上限：示例第二方向另有 2 席封顶，会干扰这个用例
        return {"item_id": name, "title": name, "score": score, "reduced": False,
                "domain": domain, "values": {"exploration": 0.1,
                "primary": 0.8, "secondary": 0.05, "task": 0.05}}

    ranked = rank([item("a%d" % i, 0.9 - i * 0.01, "architecture") for i in range(4)])
    assert len(select(ranked, core=3, exploration=0, max_per_domain=3)["core"]) == 3
    assert len(select(ranked, core=3, exploration=0, max_per_domain=1)["core"]) == 1


def test_core_caps_visual_items_at_two_without_reserving_a_seat():
    def item(name, score, values, domain):
        return {"item_id": name, "title": name, "score": score, "reduced": False,
                "domain": domain, "values": {"exploration": 0.1, **values}}

    # 示例第二方向分数很高，但最多只能占 2 席
    ranked = rank([
        item("vis1", 0.95, {"primary": 0.05, "secondary": 0.9, "task": 0.05}, "design"),
        item("vis2", 0.93, {"primary": 0.05, "secondary": 0.88, "task": 0.05}, "art"),
        item("vis3", 0.91, {"primary": 0.05, "secondary": 0.86, "task": 0.05}, "photo"),
        item("ai1", 0.60, {"primary": 0.8, "secondary": 0.05, "task": 0.1}, "ai"),
        item("ai2", 0.58, {"primary": 0.8, "secondary": 0.05, "task": 0.1}, "engineering"),
        item("ai3", 0.56, {"primary": 0.8, "secondary": 0.05, "task": 0.1}, "infra"),
    ])
    picked = select(ranked, core=5, exploration=0)
    ids = [row["item_id"] for row in picked["core"]]
    visual = [name for name in ids if name.startswith("vis")]
    assert len(visual) == 2, ids
    assert len(ids) == 5


def test_core_can_have_zero_visual_items():
    def item(name, score):
        return {"item_id": name, "title": name, "score": score, "reduced": False,
                "domain": name, "values": {"exploration": 0.1,
                "primary": 0.8, "secondary": 0.05, "task": 0.1}}

    ranked = rank([item("ai%d" % i, 0.9 - i * 0.01) for i in range(5)])
    picked = select(ranked, core=5, exploration=0)
    assert len(picked["core"]) == 5
    assert all(not row["item_id"].startswith("vis") for row in picked["core"])
