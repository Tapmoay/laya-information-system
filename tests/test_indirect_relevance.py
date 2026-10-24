"""间接相关：领域不是独立隔间，上游变化也算方向相关。"""

from __future__ import annotations

from judgment.scoring import Weights, summarise


def _scores(**values):
    """构造 13 问的输出：值槽 + 形态 + 质量。"""
    base = {name: {"noul": val} for name, val in values.items()}
    base["quality_testable"] = {"noul": 0.7}
    base["quality_increment"] = {"noul": 0.7}
    base["form"] = {"probabilities": {"ARTICLE": 0.9}}
    return base


def test_high_environment_lifts_weak_direction():
    weak = _scores(primary=0.28, task=0.06, secondary=0.08, exploration=0.26,
                   value_environment=0.87, value_task=0.49)
    out = summarise(weak)
    assert out["indirect"] is True
    assert out["direction_used"] == 0.55
    plain = summarise(weak, Weights(indirect_environment=0.95))
    assert plain["indirect"] is False
    assert out["score"] > plain["score"]


def test_no_indirect_when_environment_is_low():
    weak = _scores(primary=0.28, task=0.06, secondary=0.08, exploration=0.26,
                   value_environment=0.40, value_task=0.49)
    assert summarise(weak)["indirect"] is False


def test_no_indirect_when_direction_is_nearly_zero():
    far = _scores(primary=0.03, task=0.02, secondary=0.01, exploration=0.10,
                  value_environment=0.90, value_task=0.40)
    assert summarise(far)["indirect"] is False


def test_direct_relevance_is_not_downgraded():
    strong = _scores(primary=0.85, task=0.7, secondary=0.1, exploration=0.1,
                     value_environment=0.5, value_task=0.6)
    out = summarise(strong)
    assert out["direction_used"] == out["direction_max"]
