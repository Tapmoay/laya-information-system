"""目标标签：多值，按阈值打，不单选。"""

from __future__ import annotations

from judgment.labels import CURRENT_TASK, LONG_TERM, VISUAL, goal_labels


def test_single_label():
    assert goal_labels({"primary": 0.8, "task": 0.2, "secondary": 0.1}) == [LONG_TERM]


def test_multi_label_when_both_high():
    assert goal_labels({"primary": 0.8, "task": 0.7, "secondary": 0.1}) == [LONG_TERM, CURRENT_TASK]


def test_no_label_when_all_low():
    assert goal_labels({"primary": 0.3, "task": 0.4, "secondary": 0.2}) == []


def test_visual_label():
    assert VISUAL in goal_labels({"primary": 0.1, "task": 0.1, "secondary": 0.9})


def test_threshold_is_adjustable():
    values = {"primary": 0.52, "task": 0.0, "secondary": 0.0}
    assert goal_labels(values) == []
    assert goal_labels(values, threshold=0.5) == [LONG_TERM]


def test_missing_or_broken_values_are_safe():
    assert goal_labels(None) == []
    assert goal_labels({"primary": "不是数字"}) == []
