"""测量适配器边界回归测试（WP-R3 的离线一半 / CAP-MEA-01）。

保护四件事：
1. 测试桩不能冒充正式模型；
2. 注册表是身份与版本的唯一来源，适配器不能自报；
3. 未验证语言要 ABSTAIN，而不是硬判；
4. 换适配器只换测量结果，不换业务流程。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from capabilities.adapters.models.base import AdapterError, AdapterOutput
from capabilities.adapters.models.invoke import build_request, invoke_decision
from capabilities.adapters.models.measurement import to_measurement
from capabilities.adapters.models.registry import get_profile, load_profiles, validate_profiles
from capabilities.adapters.models.stub import agent_stub, visual_stub
from runtime.offline_run import load_case
from selection.engine import select
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-18T03:30:00+08:00")
OPTIONS = ["DIRECT", "ADJACENT", "EXPLORATORY", "UNRELATED", "INSUFFICIENT_EVIDENCE"]

REAL_PROFILE = {
    "model_profile_id": "MODEL-PROFILE-TEST-REAL",
    "display_name": "测试用已激活模型（无真实实现）",
    "implementation_kind": "REAL",
    "provider_or_runtime": "test",
    "upstream_model_id": "test/decision-model",
    "revision": "rev-0001",
    "license": "TEST-ONLY",
    "adapter_version": "0.1.0",
    "capabilities": ["CAP-MEA-02"],
    "declared_languages": ["en", "zh-Hans"],
    "validated_languages": ["en"],
    "context_limit_tokens": 8192,
    "resource_profile": {
        "vram_peak_mb": None,
        "ram_peak_mb": None,
        "cold_start_seconds": None,
        "per_item_seconds": None,
    },
    "eval_report_id": "eval-test-0001",
    "calibration_profile_id": None,
    "status": "ACTIVE",
    "activated_at": "2026-10-18T00:00:00+08:00",
    "retired_at": None,
    "rollback_to": None,
}


def stub_profile(profile_id: str = "MODEL-PROFILE-STUB-AGENT") -> dict:
    return get_profile(load_profiles(), profile_id)


def make_request(
    text: str = "Strands Decider 2B: typed decision model",
    judgment: str = "en",
    representation: str = "ORIGINAL_SOURCE_TEXT",
    translation_ref: str | None = None,
    options: list[str] | None = None,
) -> dict:
    return build_request(
        request_id="req-2026-10-18-0001",
        capability_id="CAP-MEA-02",
        candidate_id="cand-m1-0001",
        candidate_text=text,
        options=options or OPTIONS,
        instruction="按给定标准判定候选与当前目标的关系",
        created_at=NOW,
        source_language="en",
        judgment_language=judgment,
        input_representation=representation,
        translation_ref=translation_ref,
        evidence_refs=["store/raw/cand-m1-0001.txt#L1-L40"],
    )


# ---------------------------------------------------------------- 注册表


def test_registry_is_valid() -> None:
    profiles = load_profiles()
    assert profiles, "注册表不能为空"
    assert validate_profiles(profiles) == []


def test_stub_is_registered_but_not_activatable() -> None:
    for profile in load_profiles():
        if profile["implementation_kind"] == "STUB":
            assert profile["status"] != "ACTIVE"


# ---------------------------------------------------------------- 边界


def test_stub_is_refused_in_production() -> None:
    with pytest.raises(AdapterError):
        invoke_decision(agent_stub(), make_request(), stub_profile())


def test_stub_runs_only_when_explicitly_allowed() -> None:
    result = invoke_decision(agent_stub(), make_request(), stub_profile(), allow_unvalidated=True)
    assert result["status"] == "OK"
    assert result["label"] in OPTIONS
    assert result["model_profile_id"] == "MODEL-PROFILE-STUB-AGENT"
    assert result["model_revision"] == "stub-v1"
    assert validate("decision-result", result) == []


def test_adapter_version_must_match_registry() -> None:
    from capabilities.adapters.models.stub import KeywordStubAdapter

    adapter = KeywordStubAdapter(
        keywords=("model",),
        hit_label="ADJACENT",
        default_label="UNRELATED",
        adapter_version="9.9.9",
    )
    with pytest.raises(AdapterError):
        invoke_decision(adapter, make_request(), stub_profile(), allow_unvalidated=True)


def test_profile_metadata_cannot_be_spoofed() -> None:
    first = json.loads(json.dumps(REAL_PROFILE))
    second = json.loads(json.dumps(REAL_PROFILE))
    second["revision"] = "rev-0002"

    a = invoke_decision(agent_stub(), make_request(), first)
    b = invoke_decision(agent_stub(), make_request(), second)
    assert a["model_revision"] == "rev-0001"
    assert b["model_revision"] == "rev-0002"


# ---------------------------------------------------------------- 语言与失败


def test_unvalidated_language_abstains() -> None:
    result = invoke_decision(
        agent_stub(), make_request(judgment="tr"), REAL_PROFILE
    )
    assert result["status"] == "ABSTAIN"
    assert result["label"] is None
    assert result["abstention"] is True
    assert result["reason_code"] == "OUT_OF_VALIDATED_LANGUAGE_SCOPE"


def test_validated_language_produces_a_measurement() -> None:
    result = invoke_decision(agent_stub(), make_request(judgment="en"), REAL_PROFILE)
    assert result["status"] == "OK"
    measurement = to_measurement(result, make_request(judgment="en"), REAL_PROFILE, NOW)
    assert validate("measurement-record", measurement) == []
    assert measurement["language_context"]["judgment_language"] == "en"


def test_adapter_exception_becomes_error() -> None:
    class Exploding:
        adapter_version = "0.1.0"

        def evaluate(self, request):
            raise RuntimeError("CUDA out of memory")

    result = invoke_decision(Exploding(), make_request(), stub_profile(), allow_unvalidated=True)
    assert result["status"] == "ERROR"
    assert result["reason_code"] == "ADAPTER_ERROR"
    assert result["label"] is None


def test_label_outside_options_is_invalid() -> None:
    class OffMenu:
        adapter_version = "0.1.0"

        def evaluate(self, request):
            return AdapterOutput(status="OK", label="SOMETHING_ELSE")

    result = invoke_decision(OffMenu(), make_request(), stub_profile(), allow_unvalidated=True)
    assert result["status"] == "INVALID"
    assert result["reason_code"] == "CONTRACT_VIOLATION"


# ---------------------------------------------------------------- 映射


def test_unvalidated_measurement_is_flagged() -> None:
    request = make_request()
    result = invoke_decision(agent_stub(), request, stub_profile(), allow_unvalidated=True)
    measurement = to_measurement(result, request, stub_profile(), NOW, unvalidated=True)
    assert measurement["uncertainties"]
    assert measurement["method"]["kind"] == "RULE"


def test_translation_path_keeps_reference() -> None:
    request = make_request(representation="TRANSLATION", translation_ref="tr-2026-10-18-0001")
    result = invoke_decision(agent_stub(), request, stub_profile(), allow_unvalidated=True)
    measurement = to_measurement(result, request, stub_profile(), NOW, unvalidated=True)
    assert measurement["language_context"]["input_representation"] == "TRANSLATION"
    assert measurement["language_context"]["translation_ref"] == "tr-2026-10-18-0001"


# ---------------------------------------------------------------- 换模型不改流程


def _candidate_text(candidate: dict) -> str:
    return " ".join(
        [candidate["title"]["text"]] + [claim["text"] for claim in candidate.get("raw_claims", [])]
    )


def _measurements_with(adapter, profile: dict) -> list[dict]:
    case = load_case(ROOT / "fixtures" / "m1" / "basic")
    produced: list[dict] = []
    for candidate in case.candidates:
        if candidate["candidate_id"] != "cand-m1-0001":
            continue
        request = build_request(
            request_id="req-2026-10-18-0001",
            capability_id="CAP-MEA-02",
            candidate_id=candidate["candidate_id"],
            candidate_text=_candidate_text(candidate),
            options=OPTIONS,
            instruction="按给定标准判定候选与当前目标的关系",
            created_at=NOW,
            source_language="en",
            judgment_language="en",
            evidence_refs=["store/raw/cand-m1-0001.txt#L1-L40"],
        )
        result = invoke_decision(adapter, request, profile, allow_unvalidated=True)
        produced.append(to_measurement(result, request, profile, NOW, unvalidated=True))

    kept = [
        item
        for item in case.measurements
        if not (item["candidate_id"] == "cand-m1-0001" and item["measurement_type"] == "RELEVANCE")
    ]
    return kept + produced


def test_swapping_adapters_changes_measurements_not_the_flow() -> None:
    case = load_case(ROOT / "fixtures" / "m1" / "basic")

    with_agent = select(case.candidates, _measurements_with(agent_stub(), stub_profile()), case.demand, case.config)
    with_visual = select(
        case.candidates,
        _measurements_with(visual_stub(), stub_profile("MODEL-PROFILE-STUB-VISUAL")),
        case.demand,
        case.config,
    )

    decisions_agent = {item["candidate_id"]: item for item in with_agent.decisions}
    decisions_visual = {item["candidate_id"]: item for item in with_visual.decisions}

    assert decisions_agent["cand-m1-0001"]["eligibility"]["decision"] == "ELIGIBLE"
    assert decisions_visual["cand-m1-0001"]["eligibility"]["decision"] == "INELIGIBLE"

    for decision in list(decisions_agent.values()) + list(decisions_visual.values()):
        assert validate("selection-decision", decision) == []

    # 业务代码（Selector）一行没改：两个适配器都只是换了测量输入
    assert with_agent.delivery_order != with_visual.delivery_order


def test_measurement_from_stub_is_a_valid_contract_object() -> None:
    request = make_request()
    result = invoke_decision(agent_stub(), request, stub_profile(), allow_unvalidated=True)
    measurement = to_measurement(result, request, stub_profile(), NOW, unvalidated=True)
    assert validate("measurement-record", measurement) == []
    assert measurement["capability_id"] == "CAP-MEA-02"
    assert measurement["measurement_type"] == "RELEVANCE"
