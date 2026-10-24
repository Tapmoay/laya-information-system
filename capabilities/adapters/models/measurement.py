"""把 DecisionResult 映射成 MeasurementRecord。

映射规则集中在这里，业务层因此不需要知道"标签集""方法类型""证据从哪来"。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
LABEL_SETS_PATH = ROOT / "contracts" / "label-sets.yaml"


def _sets() -> list[dict[str, Any]]:
    data = yaml.safe_load(LABEL_SETS_PATH.read_text(encoding="utf-8")) or {}
    return list(data.get("sets") or [])


def measurement_type_for(capability_id: str) -> str:
    for item in _sets():
        if capability_id in item["capability_ids"]:
            return item["measurement_type"]
    raise KeyError(f"{capability_id} 没有对应的测量类型")


def label_set_for(measurement_type: str) -> str:
    for item in _sets():
        if item["measurement_type"] == measurement_type:
            return item["label_set"]
    raise KeyError(f"{measurement_type} 没有对应的标签集")


def to_measurement(
    result: dict[str, Any],
    request: dict[str, Any],
    profile: dict[str, Any],
    measured_at: datetime,
    unvalidated: bool = False,
) -> dict[str, Any]:
    measurement_type = measurement_type_for(result["capability_id"])
    status = result["status"]

    state: dict[str, Any] = {"status": status}
    if status != "OK":
        state["reason_code"] = result["reason_code"]

    value = None
    if status == "OK":
        value = {
            "label": result["label"],
            "label_set": label_set_for(measurement_type),
            "score": None,
            "probabilities": result["probabilities"],
        }

    evidence = list(result["evidence_refs"]) or list(request["state"].get("evidence_refs") or [])
    uncertainties: list[str] = []
    if unvalidated:
        uncertainties.append("适配器为未验证测试桩，结果不得进入正式判断")

    language = request["language_context"]
    return {
        "measurement_id": f"meas-{request['request_id']}",
        "candidate_id": request["state"]["candidate_id"],
        "capability_id": result["capability_id"],
        "measurement_type": measurement_type,
        "value": value,
        "state": state,
        "evidence_refs": evidence,
        "contradictions": [],
        "uncertainties": uncertainties,
        "language_context": {
            "source_language": language["source_language"],
            "judgment_language": result["judgment_language"],
            "input_representation": result["input_representation"],
            "translation_ref": result.get("translation_ref"),
        },
        "method": {
            "kind": "MODEL" if profile["implementation_kind"] == "REAL" else "RULE",
            "model_profile_id": result["model_profile_id"],
            "model_revision": result["model_revision"],
            "adapter_version": result["adapter_version"],
            "calibration_profile_id": result["calibration_profile_id"],
        },
        "measured_at": measured_at.isoformat(timespec="seconds"),
        "contract_version": "1.1",
        "trace_ref": result["inference_metadata_ref"],
    }
