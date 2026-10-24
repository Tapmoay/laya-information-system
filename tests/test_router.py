"""路由：模型给概率，代码决定直接执行还是交给 Agent。"""

from __future__ import annotations

from judgment.router import AUTO, ESCALATE, apply, route


def test_confident_distribution_is_auto() -> None:
    decision = route({"DIRECT": 0.9, "ADJACENT": 0.05, "UNRELATED": 0.05})
    assert decision.route == AUTO
    assert decision.label == "DIRECT"
    assert decision.reason == "CONFIDENT"


def test_low_confidence_escalates() -> None:
    decision = route({"DIRECT": 0.4, "ADJACENT": 0.35, "UNRELATED": 0.25})
    assert decision.route == ESCALATE
    assert decision.reason == "LOW_CONFIDENCE"


def test_close_call_escalates_when_top_is_acceptable_but_runner_up_is_close() -> None:
    # 默认阈值（0.75/0.25）下 CLOSE_CALL 几乎不可达：top≥0.75 且 second≥0.5 会超过 1。
    # 放宽 accept 后可以看到它就位。
    decision = route({"DIRECT": 0.62, "ADJACENT": 0.38}, accept_threshold=0.6, margin_threshold=0.25)
    assert decision.route == ESCALATE
    assert decision.reason == "CLOSE_CALL"


def test_default_thresholds_prefer_low_confidence() -> None:
    decision = route({"DIRECT": 0.52, "ADJACENT": 0.48})
    assert decision.reason == "LOW_CONFIDENCE"


def test_missing_or_broken_probabilities_escalate() -> None:
    assert route(None).reason == "NO_PROBABILITIES"
    assert route({}).reason == "NO_PROBABILITIES"
    assert route({"DIRECT": 0.0, "ADJACENT": 0.0}).reason == "INVALID_PROBABILITIES"
    assert route({"DIRECT": -0.2}).reason == "INVALID_PROBABILITIES"
    assert route({"DIRECT": "x"}).reason == "INVALID_PROBABILITIES"


def test_distribution_is_normalised_before_use() -> None:
    decision = route({"DIRECT": 4.5, "ADJACENT": 0.5})
    assert decision.route == AUTO
    assert abs(sum(decision.probabilities.values()) - 1.0) < 1e-9


def test_apply_marks_escalation_as_abstain() -> None:
    result = {
        "status": "OK",
        "label": "DIRECT",
        "probabilities": {"DIRECT": 0.45, "ADJACENT": 0.42},
        "capability_id": "CAP-MEA-02",
    }
    updated, decision = apply(result)
    assert decision.route == ESCALATE
    assert updated["status"] == "ABSTAIN"
    assert updated["label"] is None
    assert updated["reason_code"] == "ESCALATED_TO_AGENT"
    assert result["status"] == "OK", "原结果不能被就地修改"


def test_apply_keeps_confident_answer() -> None:
    result = {"status": "OK", "label": "DIRECT", "probabilities": {"DIRECT": 0.95, "ADJACENT": 0.05}}
    updated, decision = apply(result)
    assert decision.route == AUTO
    assert updated["status"] == "OK"
    assert updated["label"] == "DIRECT"


def test_routing_is_deterministic() -> None:
    first = route({"DIRECT": 0.8, "ADJACENT": 0.2})
    second = route({"ADJACENT": 0.2, "DIRECT": 0.8})
    assert first.as_dict() == second.as_dict()
