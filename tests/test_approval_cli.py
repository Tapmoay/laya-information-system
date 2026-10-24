"""批准闸门命令行回归测试。

保护的重点不是"能激活"，而是"没读对照、没逐字确认时激活不了"。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from feedback import approve_cli, diff_cli
from feedback.policy_store import PolicyStore, RecordStore
from feedback.proposal import build_proposal
from selection.engine import load_policy
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-18T05:20:00+08:00"
CHANGE = {"pointer": "/channels/ADJACENT", "before": "CORE", "after": "EXPLORATION"}


def write_proposal(tmp_path: Path) -> tuple[Path, dict]:
    policy = load_policy()
    proposal = build_proposal(
        policy,
        proposal_id="prop-cli-01",
        rule_id="RULE-ADJACENT-CHANNEL",
        rationale="邻接内容长期挤占核心通道的交付位",
        trigger_observation="连续三轮核心交付都是邻接相关内容",
        evidence_refs=["runs/2026-10-18/run-r2/run-manifest.json"],
        changes=[CHANGE],
        expected_effect="核心通道只保留直接相关内容",
        side_effects=["可能让邻接内容失去核心位曝光"],
        review_condition="两周后检查核心通道是否长期为空",
        rollback_condition="核心通道连续五轮无交付",
        created_at=datetime.fromisoformat(NOW),
    )
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, proposal


def make_diff(tmp_path: Path, proposal_path: Path) -> Path:
    out = tmp_path / "diff.json"
    code = diff_cli.main([
        "--proposal", str(proposal_path),
        "--case", str(ROOT / "fixtures" / "m1" / "basic"),
        "--out", str(out),
        "--at", NOW,
    ])
    assert code == 0
    return out


def test_diff_cli_writes_a_valid_report(tmp_path: Path) -> None:
    proposal_path, proposal = write_proposal(tmp_path)
    diff_path = make_diff(tmp_path, proposal_path)
    report = json.loads(diff_path.read_text(encoding="utf-8"))
    assert validate("policy-diff-report", report) == []
    assert report["proposal_ref"] == proposal["proposal_id"]
    assert report["squeezes"], "改变通道时必然有挤压"
    moved = [
        item for item in report["affected_candidates"]
        if item.get("from_channel") != item.get("to_channel")
    ]
    assert moved and moved[0]["to_channel"] == "EXPLORATION"


def test_approval_requires_exact_confirmation(tmp_path: Path) -> None:
    proposal_path, _ = write_proposal(tmp_path)
    diff_path = make_diff(tmp_path, proposal_path)
    store = tmp_path / "versions"

    code = approve_cli.main([
        "--proposal", str(proposal_path),
        "--diff", str(diff_path),
        "--store", str(store),
        "--approver", "用户",
        "--confirm", "RULE-SOMETHING-ELSE",
        "--at", NOW,
    ])

    assert code == 2
    assert not store.exists()


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    proposal_path, _ = write_proposal(tmp_path)
    diff_path = make_diff(tmp_path, proposal_path)
    store = tmp_path / "versions"

    code = approve_cli.main([
        "--proposal", str(proposal_path),
        "--diff", str(diff_path),
        "--store", str(store),
        "--approver", "用户",
        "--confirm", "RULE-ADJACENT-CHANNEL",
        "--dry-run",
        "--at", NOW,
    ])

    assert code == 0
    assert not store.exists()


def test_approval_writes_version_and_record(tmp_path: Path) -> None:
    proposal_path, proposal = write_proposal(tmp_path)
    diff_path = make_diff(tmp_path, proposal_path)
    store = tmp_path / "versions"
    records = tmp_path / "records"

    code = approve_cli.main([
        "--proposal", str(proposal_path),
        "--diff", str(diff_path),
        "--store", str(store),
        "--records", str(records),
        "--approver", "用户",
        "--confirm", "RULE-ADJACENT-CHANNEL",
        "--note", "看过挤压了",
        "--at", NOW,
    ])

    assert code == 0
    assert PolicyStore(store).versions() == ["0.1.0", "0.2.0"]
    activation = RecordStore(records).read("activations")[0]
    payload = {key: value for key, value in activation.items() if key != "_id"}
    assert validate("policy-activation", payload) == []
    assert payload["approved_by"] == "用户"
    assert payload["proposal_ref"] == proposal["proposal_id"]


def test_approval_rejects_a_report_from_another_proposal(tmp_path: Path) -> None:
    proposal_path, _ = write_proposal(tmp_path)
    diff_path = make_diff(tmp_path, proposal_path)
    report = json.loads(diff_path.read_text(encoding="utf-8"))
    report["proposal_ref"] = "prop-other"
    diff_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    code = approve_cli.main([
        "--proposal", str(proposal_path),
        "--diff", str(diff_path),
        "--store", str(tmp_path / "versions"),
        "--approver", "用户",
        "--confirm", "RULE-ADJACENT-CHANNEL",
        "--at", NOW,
    ])
    assert code == 2
