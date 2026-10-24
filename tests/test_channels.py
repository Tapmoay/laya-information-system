"""三通道与三态：分数只决定"要不要", 通道决定"为什么留"。"""

from __future__ import annotations

from judgment.channels import CHANNELS, DEEP, DROP, SAVE, assign, decide, label
from judgment.channels import commercial_intent


def test_task_value_routes_to_a():
    result = assign({"task": 0.8, "primary": 0.4, "value_environment": 0.2, "exploration": 0.1})
    assert result["channel"] == "A"


def test_environment_value_routes_to_b_even_if_not_immediately_usable():
    """变化监测：现在用不上也要留。"""
    result = assign({"task": 0.1, "primary": 0.2, "value_environment": 0.7, "exploration": 0.1})
    assert result["channel"] == "B"


def test_aesthetic_and_exploration_route_to_c():
    result = assign({"task": 0.05, "primary": 0.1, "value_environment": 0.2, "exploration": 0.3, "secondary": 0.85})
    assert result["channel"] == "C"


def test_tie_prefers_actionable_channel():
    result = assign({"task": 0.6, "primary": 0.0, "value_environment": 0.6, "exploration": 0.0})
    assert result["channel"] == "A"


def test_missing_values_do_not_crash():
    result = assign(None)
    assert result["channel"] in CHANNELS


def test_states_follow_two_lines():
    assert decide(0.75) == DEEP
    assert decide(0.50) == SAVE
    assert decide(0.10) == DROP


def test_state_handles_bad_score():
    assert decide("not-a-number") == DROP  # type: ignore[arg-type]


def test_labels_are_chinese():
    assert label(DEEP) == "值得深入" and label(SAVE) == "值得暂存" and label(DROP) == "直接放弃"

def test_pure_marketing_is_flagged():
    """只喊品牌、联名、代言，不给任何做法——这种不该进 C。"""
    text = "品牌方宣布联名，代言人出席发布会，本轮营销投放覆盖多个渠道。" * 3
    assert commercial_intent(text) is True


def test_marketing_is_marked_not_removed():
    """商业内容照进 C，只在结果里带标记——剔不剔除由人决定，不由词表决定。"""
    text = ("品牌发布会现场，车辆依靠风扇吸附产生负压，弯道横向加速度超过 2g，"
            "下压力读数 4700kg，原理与赛车地面效应类似。" * 3)
    result = assign({"secondary": 0.9, "task": 0.1}, form="MARKETING", text=text)
    assert result["commercial"] is True
    assert result["scores"]["C"] > 0  # 没有被清零


def test_craft_case_is_flagged_too_but_allowed():
    text = ("品牌这次 campaign 的画面里，构图把主体压在左下，光影从侧面切进来，"
            "材质的反差撑起了整张海报。" * 3)
    result = assign({"secondary": 0.9}, form="MARKETING", text=text)
    assert result["commercial"] is True
    assert result["scores"]["C"] > 0
