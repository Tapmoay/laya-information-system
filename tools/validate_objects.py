"""对象契约校验器（WP-R2）。

两层校验：
  1. JSON Schema：结构、必填、跨字段条件（if/then）、$ref 解析。
  2. 语义校验：schema 表达不了的规则，比如"标签必须属于该测量类型的标签集"、
     "原因码必须与状态相容"。

用法：
    python tools/validate_objects.py --type candidate-record fixtures/contracts/candidate-record/valid/minimal.json
    python tools/validate_objects.py --all-fixtures
    python tools/validate_objects.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

import jsonschema
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
LABEL_SETS_PATH = ROOT / "contracts" / "label-sets.yaml"
FIXTURE_DIR = ROOT / "fixtures" / "contracts"
MANIFEST_PATH = FIXTURE_DIR / "manifest.yaml"

# 对象类型 -> (契约标识, schema 文件名)
OBJECTS: dict[str, tuple[str, str]] = {
    "policy-bundle": ("POLICY-BUNDLE/1.0", "policy-bundle-1.0.schema.json"),
    "policy-config": ("POLICY-CONFIG/1.0", "policy-config-1.0.schema.json"),
    "demand-snapshot": ("DEMAND-SNAPSHOT/1.0", "demand-snapshot-1.0.schema.json"),
    "candidate-record": ("CANDIDATE-RECORD/1.0", "candidate-record-1.0.schema.json"),
    "measurement-record": ("MEASUREMENT-RECORD/1.0", "measurement-record-1.0.schema.json"),
    "selection-decision": ("SELECTION-DECISION/1.0", "selection-decision-1.0.schema.json"),
    "delivery-object": ("DELIVERY-OBJECT/1.0", "delivery-object-1.0.schema.json"),
    "run-manifest": ("RUN-MANIFEST/1.0", "run-manifest-1.0.schema.json"),
    "trigger-record": ("TRIGGER-RECORD/1.0", "trigger-record-1.0.schema.json"),
    "rendered-brief": ("RENDERED-BRIEF/1.0", "rendered-brief-1.0.schema.json"),
    "delivery-status": ("DELIVERY-STATUS/1.0", "delivery-status-1.0.schema.json"),
    "decision-request": ("DECISION-REQUEST/1.1", "decision-request-1.1.schema.json"),
    "decision-result": ("DECISION-RESULT/1.1", "decision-result-1.1.schema.json"),
    "model-profile": ("MODEL-PROFILE/1.0", "model-profile-1.0.schema.json"),
    "fetch-result": ("FETCH-RESULT/1.0", "fetch-result-1.0.schema.json"),
    "source-identity": ("SOURCE-IDENTITY/1.0", "source-identity-1.0.schema.json"),
    "language-observation": ("LANGUAGE-OBSERVATION/1.0", "language-observation-1.0.schema.json"),
    "feedback-record": ("FEEDBACK-RECORD/1.0", "feedback-record-1.0.schema.json"),
    "outcome-record": ("OUTCOME-RECORD/1.0", "outcome-record-1.0.schema.json"),
    "policy-proposal": ("POLICY-PROPOSAL/1.0", "policy-proposal-1.0.schema.json"),
    "policy-diff-report": ("POLICY-DIFF-REPORT/1.0", "policy-diff-report-1.0.schema.json"),
    "policy-activation": ("POLICY-ACTIVATION/1.0", "policy-activation-1.0.schema.json"),
    "observation": ("OBSERVATION/1.0", "observation-1.0.schema.json"),
}

STATUSES = ("OK", "MISSING", "UNKNOWN", "NOT_APPLICABLE", "INVALID", "ABSTAIN", "ERROR", "PARTIAL")

# 状态与原因码的相容表（docs/03-error-and-unknown-semantics.md §2、§6）。
REASON_ALLOWED: dict[str, set[str]] = {
    "OK": set(),
    "MISSING": {"NOT_CONFIGURED", "DEPENDENCY_MISSING"},
    "UNKNOWN": {"SOURCE_UNREACHABLE", "SOURCE_GONE", "SOURCE_PAYWALLED", "EVIDENCE_CONFLICT", "EVIDENCE_INSUFFICIENT"},
    "NOT_APPLICABLE": {"NOT_APPLICABLE_FIELD"},
    "INVALID": {"CONTRACT_VIOLATION", "VERSION_CONFLICT"},
    "ABSTAIN": {"OUT_OF_VALIDATED_LANGUAGE_SCOPE", "OUT_OF_VALIDATED_DOMAIN", "EVIDENCE_INSUFFICIENT", "ESCALATED_TO_AGENT"},
    "ERROR": {"TIMEOUT", "MODEL_LOAD_FAILED", "PUSH_FAILED", "DEPENDENCY_MISSING", "SOURCE_UNREACHABLE", "ADAPTER_ERROR", "SOURCE_GONE", "SOURCE_PAYWALLED", "ACCESS_DISALLOWED"},
    "PARTIAL": {"CONTEXT_TRUNCATED", "SOURCE_PAYWALLED", "EVIDENCE_INSUFFICIENT", "SOURCE_GONE", "VERSION_CONFLICT"},
}


def _build_registry() -> Registry:
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        resources.append(
            (doc["$id"], Resource.from_contents(doc, default_specification=DRAFT202012))
        )
    return Registry().with_resources(resources)


REGISTRY = _build_registry()


def load_label_sets() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(LABEL_SETS_PATH.read_text(encoding="utf-8"))
    return {item["measurement_type"]: item for item in data["sets"]}


STATE_KEYS = {"status", "reason_code", "detail_ref", "degraded_from", "degrade_reason"}


def iter_states(node: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    """递归找出所有状态对象。

    判据必须同时满足：status 取值在状态枚举内，且**没有任何额外字段**。
    只靠 status 会误伤 RunManifest 的阶段条目——阶段状态也有 OK / PARTIAL，
    但它们不是"带原因码的状态对象"。
    """
    if isinstance(node, dict):
        if (
            isinstance(node.get("status"), str)
            and node["status"] in STATUSES
            and set(node) <= STATE_KEYS
        ):
            yield path, node
        for key, value in node.items():
            yield from iter_states(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_states(value, f"{path}/{index}")


def semantic_checks(kind: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    # 1. 状态与原因码必须相容
    for path, state in iter_states(payload):
        status = state["status"]
        code = state.get("reason_code")
        if status == "OK":
            if code:
                errors.append(f"{path}: 状态 OK 不应携带 reason_code（得到 {code}）")
            continue
        if code is None:
            errors.append(f"{path}: 状态 {status} 必须给出 reason_code")
        elif code not in REASON_ALLOWED[status]:
            errors.append(
                f"{path}: reason_code={code} 与状态 {status} 不相容（允许：{sorted(REASON_ALLOWED[status])}）"
            )

    if kind == "measurement-record":
        errors += _check_measurement_labels(payload)
    if kind == "candidate-record":
        errors += _check_candidate_time_order(payload)
    if kind == "delivery-object":
        errors += _check_delivery_ranks(payload)
    if kind == "run-manifest":
        errors += _check_manifest_times(payload)
    if kind == "policy-config":
        errors += _check_capacity_sources(payload)
    if kind == "policy-diff-report":
        errors += _check_diff_squeezes(payload)
    return errors


def _check_diff_squeezes(payload: dict[str, Any]) -> list[str]:
    """只要有任何通道变少，就必须写出被挤压的部分：只报好消息的对照等于没做。"""
    shrunk = [
        delta for delta in payload.get("channel_deltas", [])
        if delta.get("after", 0) < delta.get("before", 0)
    ]
    if shrunk and not payload.get("squeezes"):
        return [f"/squeezes: 有 {len(shrunk)} 个通道变少，但 squeezes 为空"]
    return []


def _check_delivery_ranks(payload: dict[str, Any]) -> list[str]:
    ranks = [item.get("priority_rank") for item in payload.get("items", [])]
    if ranks != list(range(1, len(ranks) + 1)):
        return [f"/items: priority_rank 必须是从 1 开始的连续名次，实得 {ranks}"]
    return []


def _check_manifest_times(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    started = payload.get("started_at")
    finished = payload.get("finished_at")
    if started and finished:
        try:
            if _parse_ts(finished) < _parse_ts(started):
                errors.append("/finished_at: 结束时间早于开始时间")
        except ValueError:
            errors.append("/finished_at: 时间无法解析")
    if payload.get("delivery_status") == "SUCCESS" and payload.get("counts", {}).get("delivered", 0) < 1:
        errors.append("/counts/delivered: 推送状态为 SUCCESS，但交付计数为 0")
    return errors


def _check_capacity_sources(payload: dict[str, Any]) -> list[str]:
    capacity = payload.get("capacity", {})
    covered = {item.get("field") for item in payload.get("capacity_sources", [])}
    missing = set(capacity) - covered
    if missing:
        return [f"/capacity_sources: 以下容量字段缺少条款来源 {sorted(missing)}"]
    return []


def _check_measurement_labels(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    measurement_type = payload.get("measurement_type")
    label_set = load_label_sets().get(measurement_type or "")
    if label_set is None:
        return [f"/measurement_type: 没有为 {measurement_type} 定义标签集"]

    value = payload.get("value")
    status = payload.get("state", {}).get("status")
    if status != "OK":
        return errors
    if not isinstance(value, dict):
        return ["/value: 状态为 OK 时必须给出测量值"]

    if value.get("label_set") != label_set["label_set"]:
        errors.append(
            f"/value/label_set: 期望 {label_set['label_set']}，得到 {value.get('label_set')}"
        )
    if value.get("label") not in label_set["labels"]:
        errors.append(
            f"/value/label: '{value.get('label')}' 不在 {label_set['label_set']} 的允许标签内"
        )
    if payload.get("capability_id") not in label_set["capability_ids"]:
        errors.append(
            f"/capability_id: {payload.get('capability_id')} 未被授权产出 {measurement_type} "
            f"（授权：{label_set['capability_ids']}）"
        )
    return errors


def _parse_ts(value: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _check_candidate_time_order(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    observed = payload.get("observed_at")
    published = payload.get("published_at") or {}
    if observed and published.get("state", {}).get("status") == "OK" and published.get("value"):
        try:
            if _parse_ts(published["value"]) > _parse_ts(observed):
                errors.append("/published_at: 发布时间晚于观测时间，时间线不成立")
        except ValueError:
            errors.append("/published_at: 时间无法解析")
    return errors


def validate(kind: str, payload: dict[str, Any]) -> list[str]:
    if kind not in OBJECTS:
        raise KeyError(f"未知对象类型 {kind}，可用：{sorted(OBJECTS)}")
    _, filename = OBJECTS[kind]
    schema = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, registry=REGISTRY)

    errors = []
    for err in validator.iter_errors(payload):
        location = "/".join(str(part) for part in err.absolute_path) or "<root>"
        errors.append(f"{location}: {err.message}")
    if errors:
        return errors
    return semantic_checks(kind, payload)


def run_fixtures() -> tuple[int, list[str]]:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    total = 0
    for case in manifest["fixtures"]:
        total += 1
        path = FIXTURE_DIR / case["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = validate(case["type"], payload)
        expect = case["expect"]
        if expect == "valid" and errors:
            failures.append(f"{case['path']}: 期望通过，实际失败 -> {errors[0]}")
        elif expect == "invalid":
            if not errors:
                failures.append(f"{case['path']}: 期望失败，实际通过")
            elif case.get("error_contains") and not any(case["error_contains"] in e for e in errors):
                failures.append(
                    f"{case['path']}: 失败原因不含 '{case['error_contains']}' -> {errors[0]}"
                )
    return total, failures


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="校验对象契约")
    parser.add_argument("--type", choices=sorted(OBJECTS))
    parser.add_argument("--all-fixtures", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    if args.list:
        for kind, (contract, filename) in sorted(OBJECTS.items()):
            print(f"{kind}: {contract} <- {filename}")
        return 0

    if args.all_fixtures:
        total, failures = run_fixtures()
        print(f"夹具 {total} 个，失败 {len(failures)} 个")
        for item in failures:
            print(f"  - {item}")
        return 0 if not failures else 1

    if not args.type or not args.files:
        parser.error("需要 --type 与至少一个文件，或使用 --all-fixtures")

    exit_code = 0
    for name in args.files:
        payload = json.loads(Path(name).read_text(encoding="utf-8"))
        errors = validate(args.type, payload)
        if errors:
            exit_code = 1
            print(f"[FAIL] {name}")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"[OK] {name}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
