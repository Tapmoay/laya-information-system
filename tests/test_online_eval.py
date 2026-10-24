"""在线调参的多用户模拟：必须能改善，且不能越界。

这个测试守的是机制而不是效果数字：效果会随数据变，
但"至少不能让任何一类用户变差，且参数始终在边界内"是硬要求。
"""

from __future__ import annotations

from pathlib import Path

from calibration.online_eval import run
from judgment.params import BOUNDS, project

SCORES = Path(__file__).resolve().parents[1] / "calibration" / "scores-crawl-ai.jsonl"


def test_adaptation_improves_or_preserves_every_user(tmp_path):
    result = run(SCORES, rounds=8, base=tmp_path)
    rows = [row for group in result["groups"].values() for row in group]
    assert rows, "模拟没有产出任何用户结果"
    for row in rows:
        assert row["adapted_precision"] >= row["prior_precision"] - 1e-9, row
    # 硬要求只有两条：不能让任何一类用户变差；至少有一类真的变好。
    # 提高多少取决于先验本身已经有多准——先验越准，可提升空间越小。
    assert sum(1 for row in rows if row["delta"] > 0) >= 1
    assert result["mean_delta"] > 0


def test_parameters_stay_inside_bounds(tmp_path):
    result = run(SCORES, rounds=8, base=tmp_path)
    for group in result["groups"].values():
        for row in group:
            params = row["final_parameters"]
            for name, (low, high) in BOUNDS.items():
                assert low - 1e-6 <= params[name] <= high + 1e-6, (row["type"], name, params[name])
            total = sum(params[name] for name in ("direction", "value", "quality", "exploration"))
            assert abs(total - 1.0) < 1e-5, (row["type"], total)


def test_simulation_is_reproducible(tmp_path):
    first = run(SCORES, rounds=8, base=tmp_path / "a")
    second = run(SCORES, rounds=8, base=tmp_path / "b")
    assert [row["final_parameters"] for row in first["groups"]["in_family"]] == [
        row["final_parameters"] for row in second["groups"]["in_family"]
    ]
    assert project(first["groups"]["in_family"][0]["final_parameters"]).digest() == project(
        second["groups"]["in_family"][0]["final_parameters"]
    ).digest()
