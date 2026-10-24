"""k 折交叉验证：小样本下至少能量化抖动。"""

from __future__ import annotations

from calibration.fit import cross_validate


def rows() -> list[dict]:
    data = []
    for index in range(20):
        for profile in ("ai", "music_art"):
            data.append(
                {
                    "item_id": f"i{index}",
                    "profile_id": profile,
                    "scores": {
                        "primary": {"noul": 0.5 + index * 0.02},
                        "exploration": {"noul": 0.0},
                        "value_task": {"noul": 0.5 + index * 0.02},
                        "quality_testable": {"noul": 0.6},
                        "quality_increment": {"noul": 0.6},
                        "form": {"probabilities": {"TECHNICAL": 1.0}},
                        "recruit": {"noul": 0.0},
                    },
                    "label": 1 if index >= 10 else 0,
                }
            )
    return data


def test_cross_validation_reports_both_profiles() -> None:
    result = cross_validate(rows(), folds=4)
    assert result["folds"] == 4 or result["folds"] >= 2
    assert set(result["by_profile"]) == {"ai", "music_art"}
    for payload in result["by_profile"].values():
        assert payload["folds"] >= 2
        assert 0.0 <= payload["mean_f1"] <= 1.0
        assert payload["min_f1"] <= payload["mean_f1"] <= payload["max_f1"]


def test_fold_count_is_capped_by_item_count() -> None:
    result = cross_validate(rows()[:2], folds=10)
    assert result["folds"] <= 2
