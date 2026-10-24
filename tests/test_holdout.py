"""留出验证：同一条内容不能同时出现在训练集和留出集。"""

from __future__ import annotations

from calibration.fit import fit_with_validation, split_rows


def rows() -> list[dict]:
    data = []
    for index in range(10):
        for profile in ("ai", "music_art"):
            data.append(
                {
                    "item_id": f"i{index}",
                    "profile_id": profile,
                    "scores": {
                        "value_task": {"noul": 0.5 + index * 0.04},
                        "quality_testable": {"noul": 0.6},
                        "quality_increment": {"noul": 0.6},
                        "form": {"probabilities": {"TECHNICAL": 1.0}},
                        "recruit": {"noul": 0.0},
                    },
                    "label": 1 if index >= 5 else 0,
                }
            )
    return data


def test_split_never_shares_items() -> None:
    train, holdout = split_rows(rows(), holdout_ratio=0.3, seed=1)
    train_items = {row["item_id"] for row in train}
    holdout_items = {row["item_id"] for row in holdout}
    assert train_items & holdout_items == set()
    assert len(holdout_items) == 3


def test_validation_reports_holdout_metrics() -> None:
    result = fit_with_validation(rows(), holdout_ratio=0.3, seed=0)
    assert "holdout" in result
    assert result["holdout_items"] == 3
    assert set(result["holdout"]["by_profile"]) == {"ai", "music_art"}
