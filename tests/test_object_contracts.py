"""对象契约回归测试（WP-R2）。

保护三件事：
  1. 夹具的期望结果必须与校验器一致（valid 过、invalid 拦）。
  2. 状态/原因码/标签集这些"语义层约束"不能被悄悄放宽。
  3. 夹具目录与清单必须同步，不能有没人跑过的孤儿夹具。
"""

from __future__ import annotations

import json

import pytest
import yaml

from tools import validate_objects as vo
from tools import validate_registry as vr


def load_fixture(relative: str) -> dict:
    return json.loads((vo.FIXTURE_DIR / relative).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 夹具


def test_all_fixtures_match_expectations() -> None:
    total, failures = vo.run_fixtures()
    assert total >= 40, "夹具数量不足，契约覆盖退化了"
    assert failures == [], failures


def test_every_object_type_has_valid_and_invalid_fixtures() -> None:
    manifest = yaml.safe_load(vo.MANIFEST_PATH.read_text(encoding="utf-8"))
    seen: dict[str, set[str]] = {}
    for case in manifest["fixtures"]:
        seen.setdefault(case["type"], set()).add(case["expect"])
    for kind in vo.OBJECTS:
        assert seen.get(kind) == {"valid", "invalid"}, f"{kind} 缺少 valid 或 invalid 夹具"


def test_every_fixture_file_is_in_manifest() -> None:
    manifest = yaml.safe_load(vo.MANIFEST_PATH.read_text(encoding="utf-8"))
    declared = {(vo.FIXTURE_DIR / case["path"]).resolve() for case in manifest["fixtures"]}
    on_disk = {path.resolve() for path in vo.FIXTURE_DIR.rglob("*.json")}
    assert on_disk == declared, f"清单与文件不一致：{on_disk ^ declared}"


# ------------------------------------------------------------------ 标签集


def test_every_measurement_capability_has_label_set() -> None:
    registry = vr.load_registry()
    sets = vo.load_label_sets()
    covered: set[str] = set()
    for item in sets.values():
        covered |= set(item["capability_ids"])

    measurement_caps = {
        cap["capability_id"] for cap in registry["capabilities"] if cap["stage"] == "MEASUREMENT"
    }
    # CAP-MEA-01 是适配器入口（映射与状态校验），本身不产出某个测量类型
    missing = measurement_caps - covered - {"CAP-MEA-01"}
    assert not missing, f"这些测量能力没有标签集：{missing}"


def test_label_sets_point_to_measurement_capabilities() -> None:
    registry = vr.load_registry()
    stage_of = {cap["capability_id"]: cap["stage"] for cap in registry["capabilities"]}
    for item in vo.load_label_sets().values():
        for capability_id in item["capability_ids"]:
            assert stage_of.get(capability_id) == "MEASUREMENT", capability_id


def test_label_sets_are_unique_per_measurement_type() -> None:
    raw = yaml.safe_load(vo.LABEL_SETS_PATH.read_text(encoding="utf-8"))
    types = [item["measurement_type"] for item in raw["sets"]]
    assert len(types) == len(set(types))


# ------------------------------------------------------------------ 状态语义


def test_every_reason_code_is_mapped_to_a_status() -> None:
    common = json.loads((vo.SCHEMA_DIR / "common-1.0.schema.json").read_text(encoding="utf-8"))
    declared = set(common["$defs"]["reason_code"]["enum"])
    mapped: set[str] = set()
    for codes in vo.REASON_ALLOWED.values():
        mapped |= codes
    assert declared == mapped, {
        "schema 有但校验器没映射": declared - mapped,
        "校验器有但 schema 没定义": mapped - declared,
    }


def test_ok_state_rejects_reason_code() -> None:
    payload = load_fixture("measurement-record/valid/relevance-model.json")
    payload["state"] = {"status": "OK", "reason_code": "TIMEOUT"}
    errors = vo.validate("measurement-record", payload)
    assert any("reason_code" in error for error in errors), errors


def test_error_state_rejects_unknown_reason_code() -> None:
    payload = load_fixture("measurement-record/valid/relevance-model.json")
    payload["value"] = None
    payload["state"] = {"status": "ERROR", "reason_code": "EVIDENCE_INSUFFICIENT"}
    errors = vo.validate("measurement-record", payload)
    assert any("不相容" in error for error in errors), errors


def test_null_language_is_rejected() -> None:
    payload = load_fixture("candidate-record/valid/minimal.json")
    payload["source_language"]["tag"] = None
    errors = vo.validate("candidate-record", payload)
    assert errors, "语种不得用 null 表示未知"


def test_sentinel_languages_are_accepted() -> None:
    payload = load_fixture("candidate-record/valid/minimal.json")
    for sentinel in ("MIXED", "UNKNOWN"):
        payload["source_language"]["tag"] = sentinel
        payload["source_language"]["method"] = "UNKNOWN" if sentinel == "UNKNOWN" else "MODEL"
        assert vo.validate("candidate-record", payload) == [], sentinel


# ------------------------------------------------------------------ 与注册表联动


def test_new_contracts_leave_the_backlog() -> None:
    backlog = vr.uncovered_schema_refs(vr.load_registry())
    for ref in (
        "POLICY-BUNDLE/1.0",
        "POLICY-CONFIG/1.0",
        "DEMAND-SNAPSHOT/1.0",
        "CANDIDATE-RECORD/1.0",
        "MEASUREMENT-RECORD/1.0",
        "SELECTION-DECISION/1.0",
        "DELIVERY-OBJECT/1.0",
        "RUN-MANIFEST/1.0",
    ):
        assert ref not in backlog, ref


def test_delivery_rank_must_be_contiguous() -> None:
    payload = load_fixture("delivery-object/valid/one-item.json")
    second = json.loads(json.dumps(payload["items"][0]))
    second["candidate_id"] = "cand-2026-10-18-0002"
    second["priority_rank"] = 3
    payload["items"].append(second)
    errors = vo.validate("delivery-object", payload)
    assert any("priority_rank" in error for error in errors), errors


def test_manifest_cannot_hide_a_failed_stage() -> None:
    payload = load_fixture("run-manifest/boundary/stage-failed.json")
    payload["run_status"] = "SUCCESS"
    errors = vo.validate("run-manifest", payload)
    assert any("run_status" in error for error in errors), errors


def test_schema_ids_are_unique() -> None:
    ids = []
    for path in vo.SCHEMA_DIR.glob("*.schema.json"):
        ids.append(json.loads(path.read_text(encoding="utf-8"))["$id"])
    assert len(ids) == len(set(ids)), ids
