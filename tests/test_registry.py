"""注册表与不变量的回归测试。

这些测试保护的是"架构约束不能被悄悄改掉"：
任何人把模型塞进 SELECTION、把依赖接反、给外部动作免授权，都会在这里失败。
"""

from __future__ import annotations

import copy

import pytest

from tools import validate_registry as vr

DOC_PATH = vr.ROOT / "docs" / "02-capability-registry.md"
BEGIN = "<!-- BEGIN GENERATED INDEX -->"
END = "<!-- END GENERATED INDEX -->"


@pytest.fixture(scope="module")
def registry() -> dict:
    return vr.load_registry()


def cap_of(registry: dict, capability_id: str) -> dict:
    for cap in registry["capabilities"]:
        if cap["capability_id"] == capability_id:
            return cap
    raise AssertionError(f"未找到能力 {capability_id}")


def all_errors(registry: dict) -> list[str]:
    return vr.validate_schema(registry) + vr.check_invariants(registry)


# ---------------------------------------------------------------- 基线


def test_schema_is_valid(registry: dict) -> None:
    assert vr.validate_schema(registry) == []


def test_all_invariants_pass(registry: dict) -> None:
    assert vr.check_invariants(registry) == []


def test_declared_invariants_match_implementation(registry: dict) -> None:
    declared = {inv["id"] for inv in registry["invariants"]}
    assert declared == set(vr.INVARIANTS)


def test_every_declared_stage_has_capabilities(registry: dict) -> None:
    used = {cap["stage"] for cap in registry["capabilities"]}
    declared = {stage["id"] for stage in registry["stages"]}
    assert used == declared


def test_business_loop_stages_present(registry: dict) -> None:
    loop = {stage["id"] for stage in registry["stages"] if stage["business_loop"]}
    assert loop == {
        "DEMAND", "INFORMATION", "MEASUREMENT",
        "SELECTION", "DELIVERY", "FEEDBACK", "POLICY_UPDATE",
    }


# ---------------------------------------------------------------- 禁止模型越权


def test_selection_capability_cannot_be_model_owned() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap = cap_of(registry, "CAP-SEL-02")
    cap["owner_type"] = "MODEL_ADAPTER"
    cap["model_required"] = True
    errors = all_errors(registry)
    assert any("INV-02" in e for e in errors), errors
    assert any("CAP-SEL-02" in e for e in errors), errors


def test_selection_capability_cannot_load_model() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap = cap_of(registry, "CAP-SEL-01")
    cap["model_required"] = True
    assert any("INV-02" in e for e in all_errors(registry))


def test_model_capability_must_declare_model_required() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap_of(registry, "CAP-MEA-03")["model_required"] = False
    assert any("INV-04" in e for e in vr.check_invariants(registry))


# ---------------------------------------------------------------- 依赖图


def test_dependency_cycle_is_detected() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap_of(registry, "CAP-SEL-01")["depends_on"].append("CAP-SEL-02")
    errors = vr.check_invariants(registry)
    assert any("成环" in e for e in errors), errors


def test_missing_dependency_is_detected() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap_of(registry, "CAP-DEL-01")["depends_on"].append("CAP-SEL-99")
    assert any("不存在" in e for e in vr.check_invariants(registry))


def test_backward_stage_dependency_is_detected() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap_of(registry, "CAP-INF-03")["depends_on"].append("CAP-DEL-01")
    errors = vr.check_invariants(registry)
    assert any("INV-11" in e for e in errors), errors


def test_dependency_graph_is_acyclic(registry: dict) -> None:
    assert not any("成环" in e for e in vr.check_invariants(registry))


# ---------------------------------------------------------------- 命名与追溯


def test_vendor_name_in_capability_is_rejected() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap_of(registry, "CAP-MEA-02")["name"] = "laya-relevance-measure"
    assert any("INV-08" in e for e in vr.check_invariants(registry))


def test_missing_trace_fields_is_rejected() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap_of(registry, "CAP-MEA-02")["trace_fields"] = []
    assert any("trace_fields" in e for e in all_errors(registry))


# ---------------------------------------------------------------- 外部副作用


def test_external_effect_requires_authorization() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap = cap_of(registry, "CAP-DEL-03")
    cap["authorization_basis"] = None
    errors = all_errors(registry)
    assert any("INV-09" in e or "authorization_basis" in e for e in errors), errors


def test_external_effect_only_in_delivery_or_policy_update() -> None:
    registry = copy.deepcopy(vr.load_registry())
    cap = cap_of(registry, "CAP-MEA-02")
    cap["external_effects"] = True
    cap["authorization_basis"] = "USER_CONFIGURED：测试用授权依据"
    assert any("INV-10" in e for e in vr.check_invariants(registry))


# ---------------------------------------------------------------- 文档与 YAML 不漂移


def test_docs_index_matches_registry(registry: dict) -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    start = text.index(BEGIN) + len(BEGIN)
    end = text.index(END)
    embedded = text[start:end].strip()
    assert embedded == vr.render_index(registry).strip(), (
        "docs/02-capability-registry.md 的索引与 registry.yaml 不一致，"
        "请运行 python tools/validate_registry.py --index 后重新粘贴"
    )


# ---------------------------------------------------------------- 待办可生成


def test_backlog_lists_contracts_not_yet_built(registry: dict) -> None:
    backlog = vr.uncovered_schema_refs(registry)
    assert backlog, "M1 阶段应当仍有未落地的对象契约，若为空说明 M2 已完成"
    assert all("/" in ref for ref in backlog)
