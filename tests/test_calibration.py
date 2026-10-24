"""参数拟合：用已知生成规则的数据检验能否把参数找回来。"""

from __future__ import annotations

import json

from calibration.dataset import load, validate_row
from calibration.fit import fit

TRUE_VALUE, TRUE_QUALITY, TRUE_REDUCED, TRUE_THRESHOLD = 0.45, 0.55, 0.15, 0.62


def make_scores(value: float, quality: float, reduced: bool) -> dict:
    return {
        "primary": {"noul": max(value, 0.3)},
        "exploration": {"noul": 0.0},
        "value_task": {"noul": value},
        "quality_testable": {"noul": quality},
        "quality_increment": {"noul": quality},
        "form": {"probabilities": {"MARKETING": 0.9, "TECHNICAL": 0.1} if reduced else {"TECHNICAL": 0.9, "MARKETING": 0.1}},
        "recruit": {"noul": 0.0},
    }


def synthetic_rows() -> list[dict]:
    rows = []
    # 三个画像，各自有不同的价值/质量分布
    profiles = {
        "ai": [(0.9, 0.8), (0.7, 0.6), (0.4, 0.5), (0.2, 0.3)],
        "music": [(0.8, 0.7), (0.6, 0.9), (0.3, 0.4), (0.1, 0.2)],
        "paint": [(0.85, 0.6), (0.5, 0.8), (0.35, 0.35), (0.15, 0.25)],
    }
    for profile, pairs in profiles.items():
        for index, (value, quality) in enumerate(pairs):
            for reduced in (False, True):
                score = TRUE_VALUE * value + TRUE_QUALITY * quality - (TRUE_REDUCED if reduced else 0)
                rows.append(
                    {
                        "item_id": f"{profile}-{index}-{int(reduced)}",
                        "profile_id": profile,
                        "scores": make_scores(value, quality, reduced),
                        "label": 1 if score >= TRUE_THRESHOLD else 0,
                    }
                )
    return rows


def test_row_validation() -> None:
    assert validate_row({"item_id": "x", "profile_id": "ai", "scores": {}, "label": 1}) == []
    problems = validate_row({"item_id": "x"})
    assert len(problems) >= 3


def test_fit_recovers_separable_parameters() -> None:
    rows = synthetic_rows()
    result = fit(rows, min_f1=0.0)
    assert "error" not in result, result
    assert result["mean_f1"] >= 0.9, result
    assert result["weights"]["direction"] >= 0.0
    assert result["mean_f1"] >= 0.9
    assert result["profiles"] == ["ai", "music", "paint"]


def test_every_profile_gets_a_score() -> None:
    result = fit(synthetic_rows(), min_f1=0.0)
    assert set(result["by_profile"]) == {"ai", "music", "paint"}
    assert all(item["items"] == 8 for item in result["by_profile"].values())


def test_impossible_constraint_is_reported() -> None:
    # F1 不可能超过 1，用一个不可达的下限验证失败路径
    result = fit(synthetic_rows(), min_f1=1.01)
    assert "error" in result


def test_loader_rejects_bad_labels(tmp_path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps({"item_id": "a", "profile_id": "ai", "scores": {}, "label": 2}) + "\n", encoding="utf-8")
    try:
        load(path)
    except ValueError as exc:
        assert "label" in str(exc)
    else:
        raise AssertionError("应当拒绝非法 label")
