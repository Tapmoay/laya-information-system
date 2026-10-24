"""测量调用边界（CAP-MEA-01）。

所有测量能力都必须经过这里：
- 统一构造 DecisionRequest / DecisionResult；
- 统一检查注册表授权、状态与已验证语言；
- 统一把异常映射为 ERROR，而不是让业务层见到 traceback；
- 统一填入 model_profile_id / revision，**不让适配器自报身份**。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from capabilities.adapters.models.base import AdapterError, DecisionCapability
from tools.validate_objects import validate

REQUEST_VERSION = "1.1"


def build_request(
    *,
    request_id: str,
    capability_id: str,
    candidate_id: str,
    candidate_text: str,
    options: list[str],
    instruction: str,
    created_at: datetime,
    source_language: str,
    judgment_language: str,
    input_representation: str = "ORIGINAL_SOURCE_TEXT",
    translation_ref: str | None = None,
    current_demand: str | None = None,
    evidence_refs: list[str] | None = None,
    delivery_language_mode: str = "ZH_TRANSLATION",
    retrieval_languages: list[str] | None = None,
) -> dict[str, Any]:
    request = {
        "request_id": request_id,
        "capability_id": capability_id,
        "state": {
            "candidate_id": candidate_id,
            "candidate_text": candidate_text,
            "current_demand": current_demand,
            "evidence_refs": list(evidence_refs or []),
        },
        "question": {
            "type": "choice",
            "instruction": instruction,
            "options": list(options),
        },
        "language_context": {
            "source_language": source_language,
            "retrieval_languages": list(retrieval_languages or [source_language]),
            "judgment_language": judgment_language,
            "input_representation": input_representation,
            "delivery_language_mode": delivery_language_mode,
            "translation_ref": translation_ref,
        },
        "created_at": created_at.isoformat(timespec="seconds"),
        "contract_version": REQUEST_VERSION,
    }
    request["state"] = {key: value for key, value in request["state"].items() if value is not None}
    problems = validate("decision-request", request)
    if problems:
        raise AdapterError("请求违反契约：" + "; ".join(problems))
    return request


def _result(
    request: dict[str, Any],
    profile: dict[str, Any],
    *,
    status: str,
    label: str | None = None,
    reason: str | None = None,
    probabilities: dict[str, float] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    language = request["language_context"]
    return {
        "request_id": request["request_id"],
        "capability_id": request["capability_id"],
        "status": status,
        "label": label,
        "probabilities": probabilities,
        "abstention": status != "OK",
        "evidence_refs": evidence
        if evidence is not None
        else list(request["state"].get("evidence_refs") or []),
        "model_profile_id": profile["model_profile_id"],
        "model_revision": profile["revision"],
        "adapter_version": profile["adapter_version"],
        "calibration_profile_id": profile.get("calibration_profile_id"),
        "judgment_language": language["judgment_language"],
        "input_representation": language["input_representation"],
        "translation_ref": language.get("translation_ref"),
        "inference_metadata_ref": f"trace/{request['request_id']}/invoke",
        "reason_code": reason,
        "contract_version": "1.1",
    }


def invoke_decision(
    adapter: DecisionCapability,
    request: dict[str, Any],
    profile: dict[str, Any],
    allow_unvalidated: bool = False,
) -> dict[str, Any]:
    problems = validate("decision-request", request)
    if problems:
        raise AdapterError("请求违反契约：" + "; ".join(problems))

    if request["capability_id"] not in profile["capabilities"]:
        raise AdapterError(
            f"{profile['model_profile_id']} 未被授权承担 {request['capability_id']}"
        )

    declared_adapter_version = getattr(adapter, "adapter_version", profile["adapter_version"])
    if declared_adapter_version != profile["adapter_version"]:
        raise AdapterError(
            f"适配器版本 {declared_adapter_version} 与注册表 {profile['adapter_version']} 不一致"
        )

    if not allow_unvalidated:
        if profile["implementation_kind"] != "REAL":
            raise AdapterError("测试桩（STUB）不能用于正式运行")
        if profile["status"] != "ACTIVE":
            raise AdapterError(f"模型状态为 {profile['status']}，不能用于正式运行")
        if (
            request["language_context"]["judgment_language"]
            not in profile["validated_languages"]
        ):
            return _result(request, profile, status="ABSTAIN", reason="OUT_OF_VALIDATED_LANGUAGE_SCOPE")

    try:
        output = adapter.evaluate(request)
    except Exception:  # noqa: BLE001 - 适配器异常必须变成可审计的 ERROR
        return _result(request, profile, status="ERROR", reason="ADAPTER_ERROR")

    if output.status == "OK":
        options = request["question"].get("options") or []
        if not output.label:
            return _result(request, profile, status="INVALID", reason="CONTRACT_VIOLATION")
        if options and output.label not in options:
            return _result(request, profile, status="INVALID", reason="CONTRACT_VIOLATION")

    result = _result(
        request,
        profile,
        status=output.status,
        label=output.label if output.status == "OK" else None,
        reason=None if output.status == "OK" else (output.reason_code or "CONTRACT_VIOLATION"),
        probabilities=output.probabilities,
        evidence=output.evidence_refs,
    )
    problems = validate("decision-result", result)
    if problems:
        raise AdapterError("适配器输出违反契约：" + "; ".join(problems))
    return result
