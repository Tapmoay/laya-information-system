"""反馈命令行回归测试。

命令行是人和系统之间的接口，它必须和契约一样严格：
不能记模糊的东西、不能重复记录、没有证据的结果不能判收益。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from feedback import cli
from feedback.detector import detect_observations
from feedback.policy_store import RecordStore
from tools.validate_objects import validate

NOW = "2026-10-18T05:00:00+08:00"


def run(argv: list[str]) -> int:
    return cli.main(argv)


def test_user_statement_roundtrip(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    code = run([
        "user-stated", "--store", str(store_dir), "--id", "fb-1",
        "--candidate", "cand-001", "--text", "这个方向我想深入", "--sentiment", "LIKE",
        "--at", NOW,
    ])
    assert code == 0
    records = RecordStore(store_dir).read("feedback")
    assert len(records) == 1
    payload = {key: value for key, value in records[0].items() if key != "_id"}
    assert validate("feedback-record", payload) == []
    assert records[0]["source"] == "USER_MESSAGE"


def test_observed_action_roundtrip(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    assert run([
        "observed", "--store", str(store_dir), "--id", "fb-2",
        "--candidate", "cand-001", "--action", "READ_TIME", "--value", "420", "--at", NOW,
    ]) == 0
    payload = {key: value for key, value in RecordStore(store_dir).read("feedback")[0].items() if key != "_id"}
    assert validate("feedback-record", payload) == []
    assert payload["observed_action"]["action"] == "READ_TIME"


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    args = ["user-stated", "--store", str(store_dir), "--id", "fb-1", "--candidate", "c-1", "--text", "第一次", "--at", NOW]
    assert run(args) == 0
    assert run(args) == 2


def test_outcome_requires_evidence_for_observed(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    code = run([
        "outcome", "--store", str(store_dir), "--id", "out-1", "--candidate", "cand-001",
        "--use", "APPLIED", "--observed-status", "OBSERVED", "--benefit", "SUPPORTED", "--at", NOW,
    ])
    assert code == 2
    assert RecordStore(store_dir).read("outcomes") == []


def test_outcome_links_existing_feedback(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    run(["user-stated", "--store", str(store_dir), "--id", "fb-1", "--candidate", "cand-001", "--text", "有用", "--at", NOW])
    code = run([
        "outcome", "--store", str(store_dir), "--id", "out-1", "--candidate", "cand-001",
        "--use", "APPLIED", "--observed-status", "OBSERVED",
        "--statement", "接进了批次脚本", "--evidence", "runs/x/run-manifest.json",
        "--benefit", "SUPPORTED", "--at", NOW,
    ])
    assert code == 0
    record = RecordStore(store_dir).read("outcomes")[0]
    assert record["feedback_refs"] == ["fb-1"]
    payload = {key: value for key, value in record.items() if key != "_id"}
    assert validate("outcome-record", payload) == []


def test_list_shows_records(tmp_path: Path, capsys) -> None:
    store_dir = tmp_path / "store"
    run(["user-stated", "--store", str(store_dir), "--id", "fb-1", "--candidate", "c-1", "--text", "第一条", "--at", NOW])
    assert run(["list", "--store", str(store_dir)]) == 0
    output = capsys.readouterr().out
    assert "feedback: 1 条" in output
    assert "fb-1" in output


def test_statement_closes_the_feedback_gap(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    manifests = [
        {"run_id": f"run-{i}", "started_at": NOW, "stages": [], "counts": {"candidates": 1, "measurements": 1, "selected": 1, "delivered": 1}}
        for i in range(3)
    ]
    before = detect_observations(manifests, RecordStore(store_dir).read("feedback"))
    assert "FEEDBACK_GAP" in {item.code for item in before}

    run(["user-stated", "--store", str(store_dir), "--id", "fb-9", "--candidate", "c-1", "--text", "这条有用", "--at", NOW])
    after = detect_observations(manifests, RecordStore(store_dir).read("feedback"))
    assert "FEEDBACK_GAP" not in {item.code for item in after}
