"""M1 离线端到端测试（WP-R5）。

验收要点（v0.3 §13 第 5 条）：固定需求 + 固定候选 + 固定测量，
能从 DemandSnapshot 走到结构化 DeliveryObject 与 RunManifest，全程不碰网络和模型。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from runtime.offline_run import run_case

ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime.fromisoformat("2026-10-18T03:00:00+08:00")


def run(case: str, out_dir: Path | None = None, run_id: str = "run-m1-test") -> dict:
    return run_case(ROOT / "fixtures" / "m1" / case, out_dir=out_dir, run_id=run_id, now=FIXED_NOW)


def test_basic_case_produces_delivery_and_manifest() -> None:
    outcome = run("basic")
    delivery = outcome["delivery"]
    manifest = outcome["manifest"]

    assert [item["candidate_id"] for item in delivery["items"]] == [
        "cand-m1-0001", "cand-m1-0002", "cand-m1-0005",
    ]
    assert [item["priority_rank"] for item in delivery["items"]] == [1, 2, 3]
    assert [item["grouping"] for item in delivery["items"]] == ["CORE", "CORE", "EXPLORATION"]
    assert manifest["run_status"] == "SUCCESS"
    assert manifest["delivery_status"] == "NOT_ATTEMPTED"
    assert manifest["counts"] == {"candidates": 5, "measurements": 16, "selected": 3, "delivered": 0}


def test_every_output_validates_against_its_contract() -> None:
    outcome = run("basic")
    from tools.validate_objects import validate

    for decision in outcome["decisions"]:
        assert validate("selection-decision", decision) == []
    assert validate("delivery-object", outcome["delivery"]) == []
    assert validate("run-manifest", outcome["manifest"]) == []


def test_empty_case_produces_an_explained_empty_delivery() -> None:
    delivery = run("empty")["delivery"]
    assert delivery["items"] == []
    assert delivery["empty_reason"]["code"] == "NO_ELIGIBLE_CANDIDATES"


def test_capacity_case_downgrades_to_monitor() -> None:
    decisions = {item["candidate_id"]: item for item in run("capacity")["decisions"]}
    assert decisions["cand-m1-0001"]["decision"] == "DELIVER"
    assert decisions["cand-m1-0002"]["decision"] == "MONITOR"


def test_run_is_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    run("basic", out_dir=first, run_id="run-repro")
    run("basic", out_dir=second, run_id="run-repro")
    for name in ("selection-decisions.json", "delivery-object.json", "run-manifest.json"):
        assert (first / name).read_text(encoding="utf-8") == (second / name).read_text(encoding="utf-8"), name


def test_artifacts_are_written_and_listed(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    outcome = run("basic", out_dir=out_dir)
    for name in ("selection-decisions.json", "delivery-object.json", "run-manifest.json"):
        assert (out_dir / name).exists()
    assert sorted(outcome["manifest"]["artifacts"]) == [
        "delivery-object.json", "rendered-brief.json", "run-manifest.json", "selection-decisions.json",
    ]
    stored = json.loads((out_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert stored["artifacts"] == outcome["manifest"]["artifacts"]


def test_invalid_input_fails_the_run_but_still_writes_a_manifest(tmp_path: Path) -> None:
    case_dir = tmp_path / "broken"
    shutil.copytree(ROOT / "fixtures" / "m1" / "basic", case_dir)
    target = case_dir / "candidates" / "cand-m1-0001.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["canonical_url"] = "not-a-url"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out_dir = tmp_path / "out"
    result = run_case(case_dir, out_dir=out_dir, now=FIXED_NOW)

    assert result["failed_stage"] == "PRECHECK"
    assert "candidate-record" in (result["error"] or "")
    assert result["manifest"]["run_status"] == "FAILED"
    assert (out_dir / "run-manifest.json").exists()
    assert not (out_dir / "delivery-object.json").exists()

    with pytest.raises(RuntimeError):
        run_case(case_dir, out_dir=tmp_path / "out2", now=FIXED_NOW, raise_on_failure=True)
