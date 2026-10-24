"""确定性选择器回归测试（WP-R4 / CAP-SEL-01..05）。

这些测试保护的是选择的性质，而不是某个具体输出：
不调用模型、不用单一总分、不把"不知道"当成"不相关"、容量来自配置。
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from runtime.offline_run import load_case, run_case
from selection.engine import load_policy, select

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORTS = {
    "torch", "transformers", "sentence_transformers", "openai", "anthropic",
    "requests", "httpx", "aiohttp", "urllib3", "langchain", "llama_cpp",
}


def by_candidate(outcome: dict) -> dict[str, dict]:
    return {item["candidate_id"]: item for item in outcome["decisions"]}


def run(case: str = "basic", **kwargs) -> dict:
    from datetime import datetime

    kwargs.setdefault("now", datetime.fromisoformat("2026-10-18T03:00:00+08:00"))
    kwargs.setdefault("run_id", "run-selection-test")
    return run_case(ROOT / "fixtures" / "m1" / case, **kwargs)


# ---------------------------------------------------------------- 架构约束


def test_selection_and_delivery_do_not_import_models_or_network() -> None:
    for package in ("selection", "delivery"):
        for path in (ROOT / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    root = module.split(".")[0]
                    assert root not in FORBIDDEN_IMPORTS, f"{path.name} 导入了 {root}"


# ---------------------------------------------------------------- 准入


def test_unrelated_candidate_is_discarded_not_delivered() -> None:
    decision = by_candidate(run())["cand-m1-0004"]
    assert decision["eligibility"]["decision"] == "INELIGIBLE"
    assert decision["decision"] == "DISCARD"
    assert decision["portfolio"] == {"channel": "NONE", "role": "NONE"}
    assert {item["code"] for item in decision["reasons"]} == {"NO_DEMAND_MATCH"}


def test_abstained_measurement_is_not_negative_evidence() -> None:
    decision = by_candidate(run())["cand-m1-0003"]
    assert decision["eligibility"]["decision"] == "ABSTAIN"
    assert decision["decision"] == "ABSTAIN"
    assert {item["code"] for item in decision["reasons"]} == {"EVIDENCE_INSUFFICIENT"}


def test_excluded_topic_blocks_candidate() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "basic")
    demand = copy.deepcopy(case.demand)
    demand["constraints"]["excluded_topics"] = ["Strands"]
    result = select(case.candidates, case.measurements, demand, case.config)
    decision = {item["candidate_id"]: item for item in result.decisions}["cand-m1-0001"]
    assert decision["eligibility"]["decision"] == "INELIGIBLE"
    assert {item["code"] for item in decision["reasons"]} == {"POLICY_EXCLUDED"}


def test_exploration_disabled_blocks_exploratory_candidates() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "basic")
    demand = copy.deepcopy(case.demand)
    demand["exploration_intent"]["enabled"] = False
    result = select(case.candidates, case.measurements, demand, case.config)
    decision = {item["candidate_id"]: item for item in result.decisions}["cand-m1-0005"]
    assert decision["eligibility"]["decision"] == "INELIGIBLE"
    assert "cand-m1-0005" not in result.delivery_order


# ---------------------------------------------------------------- 容量与组合


def test_capacity_comes_from_config_not_from_code() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "basic")
    wide = select(case.candidates, case.measurements, case.demand, case.config)
    tight_config = copy.deepcopy(case.config)
    tight_config["capacity"]["core_items_max"] = 1
    tight = select(case.candidates, case.measurements, case.demand, tight_config)

    core_wide = [cid for cid in wide.delivery_order if cid in {"cand-m1-0001", "cand-m1-0002"}]
    core_tight = [cid for cid in tight.delivery_order if cid in {"cand-m1-0001", "cand-m1-0002"}]
    assert len(core_wide) == 2
    assert len(core_tight) == 1


def test_over_capacity_candidate_becomes_monitor() -> None:
    decision = by_candidate(run("capacity"))["cand-m1-0002"]
    assert decision["decision"] == "MONITOR"
    assert "CAPACITY_LIMIT" in {item["code"] for item in decision["reasons"]}
    assert decision["portfolio"]["role"] == "MONITOR"


def _importance(measurement: dict, candidate_id: str, label: str, status: str = "OK") -> dict:
    item = copy.deepcopy(measurement)
    item["measurement_id"] = f"meas-importance-{candidate_id}"
    item["candidate_id"] = candidate_id
    item["capability_id"] = "CAP-MEA-10"
    item["measurement_type"] = "IMPORTANCE"
    item["value"] = {
        "label": label,
        "label_set": "IMPORTANCE/1.0",
        "score": None,
        "probabilities": None,
    }
    item["state"] = {"status": status}
    return item


def test_s_level_candidate_may_break_capacity() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "capacity")
    assert case.config["capacity"]["s_breakout_allowed"] is True

    baseline = {
        item["candidate_id"]: item
        for item in select(case.candidates, case.measurements, case.demand, case.config).decisions
    }
    assert baseline["cand-m1-0002"]["decision"] == "MONITOR"

    template = next(m for m in case.measurements if m["candidate_id"] == "cand-m1-0002")
    boosted = case.measurements + [_importance(template, "cand-m1-0002", "S")]
    result = select(case.candidates, boosted, case.demand, case.config)
    decision = {item["candidate_id"]: item for item in result.decisions}["cand-m1-0002"]

    assert decision["decision"] == "DELIVER"
    assert "S_LEVEL_BREAKOUT" in {item["code"] for item in decision["reasons"]}
    check = next(
        item for item in decision["constraint_checks"] if item["constraint_id"] == "CORE_CAPACITY"
    )
    assert check["actual"] > check["limit"], "破格必须如实显示超出"
    assert check["passed"] is False

    from tools.validate_objects import validate

    assert validate("measurement-record", boosted[-1]) == []
    assert validate("selection-decision", decision) == []


def test_unknown_importance_does_not_break_capacity() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "capacity")
    template = next(m for m in case.measurements if m["candidate_id"] == "cand-m1-0002")
    unknown = _importance(template, "cand-m1-0002", "INSUFFICIENT_EVIDENCE", status="ABSTAIN")
    result = select(case.candidates, case.measurements + [unknown], case.demand, case.config)
    decision = {item["candidate_id"]: item for item in result.decisions}["cand-m1-0002"]
    assert decision["decision"] == "MONITOR", "拿不准的东西不该拥有打破容量限制的权力"


def test_breakout_requires_policy_permission() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "capacity")
    config = copy.deepcopy(case.config)
    config["capacity"]["s_breakout_allowed"] = False
    template = next(m for m in case.measurements if m["candidate_id"] == "cand-m1-0002")
    boosted = case.measurements + [_importance(template, "cand-m1-0002", "S")]
    result = select(case.candidates, boosted, case.demand, config)
    decision = {item["candidate_id"]: item for item in result.decisions}["cand-m1-0002"]
    assert decision["decision"] == "MONITOR"


def test_exploration_channel_is_capped_separately() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "basic")
    config = copy.deepcopy(case.config)
    config["capacity"]["exploration_items_max"] = 0
    result = select(case.candidates, case.measurements, case.demand, config)
    assert "cand-m1-0005" not in result.delivery_order


# ---------------------------------------------------------------- 可解释与可复现


def test_every_component_keeps_its_source_measurement() -> None:
    for decision in run()["decisions"]:
        for component in decision["priority_components"]:
            assert component["source_measurement_refs"], decision["candidate_id"]
            assert component["name"] in {"TASK", "CAPABILITY", "DECISION", "EXPLORATION", "ENVIRONMENT"}


def test_selection_is_deterministic() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "basic")
    first = select(case.candidates, case.measurements, case.demand, case.config)
    second = select(case.candidates, case.measurements, case.demand, case.config)
    assert json.dumps(first.decisions, ensure_ascii=False) == json.dumps(second.decisions, ensure_ascii=False)
    assert first.delivery_order == second.delivery_order


def test_policy_is_versioned() -> None:
    policy = load_policy()
    assert policy["selector_version"]
    assert policy["policy_version"]
    assert policy["status"] in {"TRIAL", "ACTIVE"}
