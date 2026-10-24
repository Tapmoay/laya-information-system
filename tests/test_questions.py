"""问句结构固定，只有槽里的对象随需求变化。"""

from __future__ import annotations

import json
from pathlib import Path

from judgment.questions import FALLBACK, SLOT_TEMPLATES, build_questions, slot_values

ROOT = Path(__file__).resolve().parents[1]
DEMAND = json.loads((ROOT / "demand" / "demand-2026-01-01.json").read_text(encoding="utf-8"))

ARCH = {
    "active_goals": [{"statement": "建筑学与空间叙事"}],
    "active_tasks": [{"statement": "读完工建史课程"}],
    "exploration_intent": {"enabled": True},
}
MUSIC = {
    "active_goals": [{"statement": "音乐制作与声音设计"}, {"statement": "生物科技基础"}],
    "active_tasks": [{"statement": "完成一首 demo"}],
    "exploration_intent": {"enabled": True},
}


def test_structure_is_identical_across_domains() -> None:
    a = [(s.name, s.kind, s.tag) for s in build_questions(DEMAND)]
    b = [(s.name, s.kind, s.tag) for s in build_questions(ARCH)]
    c = [(s.name, s.kind, s.tag) for s in build_questions(MUSIC)]
    assert a == b == c, "换领域不应该改变问题结构"


def test_only_the_slots_change() -> None:
    arch = {s.name: s.instructions for s in build_questions(ARCH)}
    music = {s.name: s.instructions for s in build_questions(MUSIC)}
    assert arch["primary"] != music["primary"]
    assert "建筑学" in arch["primary"] and "音乐制作" in music["primary"]
    assert arch["value_capability"] == music["value_capability"], "政策问句与领域无关"


def test_missing_slots_use_fallbacks_without_adding_questions() -> None:
    minimal = {"active_goals": [], "active_tasks": [], "exploration_intent": {}}
    specs = build_questions(minimal)
    assert len(specs) == len(build_questions(DEMAND)), "缺省方向不应该改变题量"
    primary = next(s for s in specs if s.name == "primary")
    assert FALLBACK["primary"] in primary.instructions
    assert primary.available is False


def test_slot_mapping_covers_the_declared_goals() -> None:
    values = slot_values(DEMAND)
    assert values["primary"] == DEMAND["active_goals"][0]["statement"]
    assert values["secondary"] == DEMAND["active_goals"][1]["statement"]
    assert values["task"] == DEMAND["active_tasks"][0]["statement"]


def test_exactly_five_fixed_value_questions() -> None:
    specs = build_questions(DEMAND)
    values = [spec.name for spec in specs if spec.tag == "policy"]
    assert values == [
        "value_task",
        "value_capability",
        "value_decision",
        "value_exploration",
        "value_environment",
    ]
    other = [spec.name for spec in build_questions(ARCH) if spec.tag == "policy"]
    assert other == values, "五项价值不随领域改变"


def test_exploration_slot_accepts_abstract_domains() -> None:
    demand = dict(DEMAND)
    demand["exploration_intent"] = {
        "enabled": True,
        "max_items": 2,
        "domains_hint": ["「设计」在其他领域中的体现，例如游戏美术、建筑、生物结构"],
    }
    exploration = next(s for s in build_questions(demand) if s.name == "exploration")
    assert "游戏美术" in exploration.instructions
    assert "建筑" in exploration.instructions


def test_templates_are_fixed() -> None:
    texts = " ".join(SLOT_TEMPLATES.values())
    assert "{text}" in texts
    assert len(SLOT_TEMPLATES) == 4
