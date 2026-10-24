"""Agent 终选：需求原文必须进包，理由缺失必须拦，容量必须守。"""

from __future__ import annotations

from judgment.agent_select import apply_decision, build_packet, override_events

DEMAND = {
    "active_goals": [
        {"statement": "示例主方向"},
        {"statement": "示例第二方向"},
    ],
    "active_tasks": [{"statement": "按 v0.3 构建系统"}],
    "exploration_intent": {"enabled": True, "domains_hint": ["设计/构成/叙事在别处的体现"]},
    "constraints": {"daily_information_budget_minutes": 30, "energy_state": "NORMAL"},
}

CANDIDATES = [
    {
        "item_id": "item-a",
        "score": 0.8,
        "selected": True,
        "provenance": {"title": "甲"},
        "scores": {
            "primary": {"noul": 0.6}, "secondary": {"noul": 0.1}, "exploration": {"noul": 0.1},
            "task": {"noul": 0.1},
            "value_task": {"noul": 0.5}, "value_capability": {"noul": 0.5},
            "value_decision": {"noul": 0.5}, "value_exploration": {"noul": 0.5},
            "value_environment": {"noul": 0.5},
            "quality_testable": {"noul": 0.7}, "quality_increment": {"noul": 0.7},
            "form": {"probabilities": {"TECHNICAL": 0.9, "PRODUCT": 0.03, "EVENT": 0.02,
                                       "RECRUIT": 0.03, "MARKETING": 0.02}},
            "recruit": {"noul": 0.1},
        },
    },
    {
        "item_id": "item-b",
        "score": 0.6,
        "selected": False,
        "provenance": {"title": "乙"},
        "scores": {},
    },
]


def test_packet_carries_demand_verbatim_and_every_candidate():
    packet = build_packet(CANDIDATES, DEMAND, bodies={"item-a": "正文" * 50})
    assert "示例主方向" in packet
    assert "设计/构成/叙事在别处的体现" in packet
    assert "每日 30 分钟" in packet
    assert "item-a" in packet and "item-b" in packet
    assert "信号：方向 primary 0.60" in packet


def test_decision_requires_reason():
    check = apply_decision(CANDIDATES, {"picks": [{"item_id": "item-a", "reason": "  "}]})
    assert not check.ok
    assert any("缺少理由" in problem for problem in check.violations)


def test_decision_enforces_capacity():
    picks = [{"item_id": "item-a", "reason": "要", "slot": "core"}] * 6
    check = apply_decision(CANDIDATES, {"picks": picks})
    assert any("重复选择" in problem for problem in check.violations)


def test_decision_rejects_unknown_item():
    check = apply_decision(CANDIDATES, {"picks": [{"item_id": "item-zzz", "reason": "要"}]})
    assert any("未知条目" in problem for problem in check.violations)


def test_same_item_cannot_be_picked_and_excluded():
    check = apply_decision(
        CANDIDATES,
        {"picks": [{"item_id": "item-a", "reason": "要"}], "excludes": [{"item_id": "item-a", "reason": "不要"}]},
    )
    assert any("既选又排" in problem for problem in check.violations)


def test_override_events_mark_whether_it_was_already_selected():
    check = apply_decision(
        CANDIDATES,
        {
            "picks": [{"item_id": "item-b", "reason": "漏了", "slot": "core"}],
            "excludes": [{"item_id": "item-a", "reason": "广告"}],
        },
    )
    assert check.ok
    events = {event["item_id"]: event for event in override_events(check, CANDIDATES)}
    assert events["item-b"]["include"] is True
    assert events["item-b"]["was_selected"] is False
    assert events["item-a"]["include"] is False
    assert events["item-a"]["was_selected"] is True


def test_review_page_has_one_row_per_decision():
    from judgment.review_page import render

    final = {
        "core": [{"item_id": "item-a", "reason": "因为甲"}],
        "exploration": [{"item_id": "item-b", "reason": "因为乙"}],
        "excluded": [{"item_id": "item-c", "reason": "因为丙"}],
    }
    html = render(CANDIDATES, final, generated_at="2026-10-19 00:30")
    assert html.startswith("<!doctype html>")
    assert html.count("class=row ") == 3
    assert html.count("data-v=") == 9
    assert "因为甲" in html and "因为丙" in html
    assert "exportJSON" in html


def test_review_page_embeds_demand_criteria_when_given():
    from judgment.review_page import render

    final = {"core": [{"item_id": "item-a", "reason": "r"}], "exploration": [], "excluded": []}
    html = render(CANDIDATES, final, demand=DEMAND)
    assert "判断标准" in html
    assert "示例主方向" in html


def test_review_page_shows_summary_when_given():
    from judgment.review_page import render

    final = {"core": [{"item_id": "item-a", "reason": "因为甲"}], "exploration": [], "excluded": []}
    html = render(CANDIDATES, final, summaries={"item-a": "这是摘要正文。"})
    assert "class=summary" in html
    assert "这是摘要正文。" in html
    assert "理由：" in html


def test_evidence_must_come_from_the_summary():
    summaries = {"item-a": "这是一段摘要，里面有一句关于光线与尺度的话。",
                 "item-b": "另一段摘要，讲的是别的。"}
    good = {"picks": [{"item_id": "item-a", "reason": "要", "evidence": "关于光线与尺度的话"}]}
    assert apply_decision(CANDIDATES, good, summaries=summaries).ok

    bad = {"picks": [{"item_id": "item-a", "reason": "要", "evidence": "标题党写的标题"}]}
    check = apply_decision(CANDIDATES, bad, summaries=summaries)
    assert not check.ok
    assert any("不在该条摘要里" in problem for problem in check.violations)


def test_evidence_required_for_excludes_too():
    summaries = {"item-a": "摘要甲。", "item-b": "摘要乙，提到某个细节。"}
    decision = {"excludes": [{"item_id": "item-b", "reason": "不要"}]}
    check = apply_decision(CANDIDATES, decision, summaries=summaries)
    assert not check.ok
    assert any("证据太短" in problem or "不在该条摘要里" in problem for problem in check.violations)


def test_evidence_ignores_whitespace_differences():
    summaries = {"item-a": "光线 与 尺度，是空间叙事的两个把手。"}
    decision = {"picks": [{"item_id": "item-a", "reason": "要", "evidence": "空间叙事的两个把手"}]}
    assert apply_decision(CANDIDATES, decision, summaries=summaries).ok

def test_packet_states_the_principles_before_the_rules():
    """原则要出现在包的最前面——它比规则长寿，agent 先看到它再看到政策节选。"""
    packet = build_packet(CANDIDATES, DEMAND, bodies={"item-a": "正文" * 50})
    assert "判断原则" in packet
    # 没有政策原文时也要在：原则不依赖政策文件
    assert packet.index("判断原则") < packet.index("## 候选")
    assert "对自己与信息之间关系的控制" in packet
