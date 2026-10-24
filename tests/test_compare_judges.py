"""provider 对照工具：秩相关、阈值对齐、报告必须写明局限。"""

from __future__ import annotations

from tools import compare_judges as cj


def test_spearman_handles_perfect_agreement_reversal_and_ties() -> None:
    assert cj.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert cj.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    tied = cj.spearman([1, 1, 2, 3], [1, 1, 2, 3])
    assert tied is not None and abs(tied - 1.0) < 1e-9
    assert cj.spearman([1], [1]) is None


def _row(item_id: str, value: float, *, technical: float = 0.9) -> dict:
    return {
        "item_id": item_id,
        "scores": {
            "primary": {"noul": value},
            "secondary": {"noul": value / 2},
            "exploration": {"noul": value / 3},
            "task": {"noul": value},
            "value_task": {"noul": value},
            "value_capability": {"noul": value},
            "value_decision": {"noul": value},
            "value_exploration": {"noul": value},
            "value_environment": {"noul": value},
            "quality_testable": {"noul": value},
            "quality_increment": {"noul": value},
            "recruit": {"noul": 0.1},
            "form": {
                "probabilities": {
                    "TECHNICAL": technical,
                    "PRODUCT": 1 - technical,
                    "EVENT": 0.0,
                    "RECRUIT": 0.0,
                    "MARKETING": 0.0,
                }
            },
        },
    }


def test_compare_reports_overlap_and_threshold_alignment() -> None:
    items = [
        {"item_id": f"item-{index}", "title": f"标题 {index}", "text_clean": "正文。" * 100,
         "provenance": {"index_fetched_at": "2026-10-24T00:00:00+00:00"}}
        for index in range(6)
    ]
    a = {item["item_id"]: _row(item["item_id"], 0.8 - index * 0.1) for index, item in enumerate(items)}
    b = {item["item_id"]: _row(item["item_id"], 0.75 - index * 0.12) for index, item in enumerate(items)}
    report = cj.compare(a, b, items, top=3)
    assert report["n"] == 6
    assert report["rho"] is not None and report["rho"] > 0.8
    assert report["top"][3] == 1.0
    assert report["threshold_b_for_a_045"] is not None
    text = cj.render(report, scores_a_path="a.jsonl", scores_b_path="b.jsonl")
    assert "分布对齐" in text
    assert "不能回答" in text
