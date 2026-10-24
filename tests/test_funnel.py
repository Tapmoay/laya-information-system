"""两级筛选：粗选 → 摘要复判 → 候选池。"""

from __future__ import annotations

from judgment.funnel import DISAGREE, DROP, PASS, coarse, finalize, refine
from judgment.params import PRIOR


def _row(item_id: str, score_primary: float, *, quality: float = 0.6) -> dict:
    return {
        "item_id": item_id,
        "provenance": {"title": "标题 " + item_id},
        "scores": {
            "primary": {"noul": score_primary},
            "secondary": {"noul": 0.2},
            "exploration": {"noul": 0.1},
            "task": {"noul": 0.2},
            "value_task": {"noul": 0.5},
            "value_capability": {"noul": 0.5},
            "value_decision": {"noul": 0.5},
            "value_exploration": {"noul": 0.5},
            "value_environment": {"noul": 0.5},
            "quality_testable": {"noul": quality},
            "quality_increment": {"noul": quality},
            "form": {"probabilities": {"TECHNICAL": 0.9, "PRODUCT": 0.05, "EVENT": 0.02,
                                       "RECRUIT": 0.02, "MARKETING": 0.01}},
            "recruit": {"noul": 0.1},
        },
    }


def _summary_scores(value: float) -> dict:
    """第二级只判价值与质量（不看方向），所以这个桩也要把这两维一起调。"""
    scores = _row("x", value)["scores"]
    for name in ("value_task", "value_capability", "value_decision", "value_exploration", "value_environment"):
        scores[name] = {"noul": value}
    for name in ("quality_testable", "quality_increment"):
        scores[name] = {"noul": value}
    return scores


def test_coarse_keeps_only_items_over_threshold():
    rows = [_row("a", 1.0, quality=0.9), _row("b", 0.1, quality=0.1)]
    result = coarse(rows, threshold=0.4, limit=10)
    assert [item.item_id for item in result.passed_coarse] == ["a"]
    assert [item.item_id for item in result.dropped] == ["b"]


def test_coarse_caps_the_candidate_pool():
    rows = [_row(chr(ord("a") + index), 1.0, quality=0.9) for index in range(6)]
    result = coarse(rows, threshold=0.1, limit=3)
    assert len(result.passed_coarse) == 3
    assert sum(1 for item in result.dropped if "上限" in item.note) == 3


def test_refine_marks_agreement_and_disagreement():
    rows = [_row("good", 1.0, quality=0.9), _row("weak", 1.0, quality=0.9)]
    result = coarse(rows, threshold=0.4, limit=10)
    bodies = {"good": "这是一段足够长的正文内容，用来生成摘要。" * 20,
              "weak": "这也是一段足够长的正文内容，用来生成摘要。" * 20}

    def ask(text: str) -> dict:
        # 摘要里含"这也"就判低分，模拟"长文噪声把第一遍分数拉高"
        return _summary_scores(0.1 if "这也" in text else 0.9)

    result = refine(result, ask=ask, bodies=bodies, threshold=0.4)
    verdicts = {item.item_id: item.verdict for item in result.refined}
    assert verdicts["good"] == PASS
    assert verdicts["weak"] == DISAGREE


def test_finalize_only_takes_passed_items():
    rows = [_row("good", 1.0, quality=0.9), _row("weak", 1.0, quality=0.9)]
    result = coarse(rows, threshold=0.4, limit=10)
    bodies = {"good": "正文" * 200, "weak": "这也" * 200}

    def ask(text: str) -> dict:
        return _summary_scores(0.1 if "这也" in text else 0.9)

    result = refine(result, ask=ask, bodies=bodies, threshold=0.4)
    final = finalize(result)
    assert [item["item_id"] for item in final["core"]] == ["good"]
    assert [item["item_id"] for item in final["needs_review"]] == ["weak"]


def test_empty_summary_goes_to_review_not_pass():
    rows = [_row("blank", 1.0, quality=0.9)]
    result = coarse(rows, threshold=0.4, limit=10)
    result = refine(result, ask=lambda text: _summary_scores(0.9), bodies={"blank": ""}, threshold=0.4)
    assert result.refined[0].verdict == DISAGREE
    assert result.refined[0].refined_score is None
