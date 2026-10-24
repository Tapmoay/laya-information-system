"""交付呈现与推送回归测试（WP-R8 / CAP-DEL-02..04）。

保护四件事：
1. 呈现层不新增事实、不改顺序；
2. 推送幂等：同一份交付重试不会产生第二份；
3. 推送失败不否定已经完成的选择；
4. run_status 与 delivery_status 各说各话。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from delivery.push import PushConfig, idempotency_key, push_brief
from delivery.renderer import render_brief
from runtime.offline_run import run_case
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime.fromisoformat("2026-10-18T03:00:00+08:00")


def run(case: str = "basic", **kwargs) -> dict:
    kwargs.setdefault("now", FIXED_NOW)
    kwargs.setdefault("run_id", "run-delivery-test")
    return run_case(ROOT / "fixtures" / "m1" / case, **kwargs)


# ---------------------------------------------------------------- 呈现


def test_brief_preserves_order_and_candidate_set() -> None:
    outcome = run()
    delivery = outcome["delivery"]
    brief = outcome["brief"]

    assert [item["candidate_id"] for item in brief["items"]] == [
        item["candidate_id"] for item in delivery["items"]
    ]
    assert [item["priority_rank"] for item in brief["items"]] == [1, 2, 3]
    assert validate("rendered-brief", brief) == []


def test_brief_keeps_a_source_link_for_every_item() -> None:
    brief = run()["brief"]
    assert brief["items"]
    for item in brief["items"]:
        assert item["source_url"].startswith("http")
        assert item["displayed_title"]
        assert item["original_title"]["text"]


def test_brief_adds_no_facts_beyond_the_delivery_object() -> None:
    outcome = run()
    delivery = outcome["delivery"]
    brief = outcome["brief"]
    by_id = {item["candidate_id"]: item for item in delivery["items"]}

    for item in brief["items"]:
        source = by_id[item["candidate_id"]]
        assert item["original_title"] == source["original_title"]
        assert item["source_url"] == source["source_url"]
        assert item["action"] == source["recommended_action"]
        assert item["grouping"] == source["grouping"]
        assert item["caveats"] == source["required_caveats"]
        assert set(item["why"]) == set(source["rationale_refs"])


def test_empty_delivery_is_explained_in_the_brief() -> None:
    outcome = run("empty")
    brief = outcome["brief"]
    assert brief["items"] == []
    assert "没有合格候选" in brief["body_markdown"]


def test_brief_warns_when_translation_is_missing() -> None:
    brief = run()["brief"]
    assert brief["language_mode"] == "ZH_TRANSLATION"
    assert any("译文" in warning for warning in brief["warnings"])


# ---------------------------------------------------------------- 推送


def test_push_writes_artifact_and_status(tmp_path: Path) -> None:
    out = tmp_path / "run"
    outcome = run(out_dir=out, push_config={"delivery": {"channel": "file", "target": str(tmp_path / "outbox")}})

    status = outcome["push_status"]
    assert status["status"] == "SUCCESS"
    assert status["attempts"] == 1
    assert validate("delivery-status", status) == []
    artifact = Path(status["artifact_ref"])
    assert artifact.exists()
    assert outcome["manifest"]["delivery_status"] == "SUCCESS"
    assert outcome["manifest"]["counts"]["delivered"] == 3


def test_push_is_idempotent_across_retries(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    config = {"delivery": {"channel": "file", "target": str(outbox)}}

    first = run(out_dir=tmp_path / "r1", push_config=config)
    second = run(out_dir=tmp_path / "r2", push_config=config)

    assert first["push_status"]["status"] == "SUCCESS"
    assert second["push_status"]["status"] == "ALREADY_SENT"
    assert first["push_status"]["idempotency_key"] == second["push_status"]["idempotency_key"]

    artifacts = list(outbox.glob("*.md"))
    assert len(artifacts) == 1
    ledger = (outbox / ".push-ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(ledger) == 1


def test_push_key_ignores_render_time_but_tracks_items() -> None:
    outcome = run()
    brief = outcome["brief"]
    key_before = idempotency_key(brief, "FILE")

    # 只改渲染时间与正文排版：不应当算作新内容
    same_content = json.loads(json.dumps(brief))
    same_content["rendered_at"] = "2026-10-18T04:00:00+08:00"
    same_content["body_markdown"] += "\n（重新排版）"
    assert idempotency_key(same_content, "FILE") == key_before

    # 条目变了：必须算作新内容
    changed = json.loads(json.dumps(brief))
    changed["items"][0]["caveats"].append("新增一条警示")
    assert idempotency_key(changed, "FILE") != key_before


def test_channel_none_means_no_push_attempt() -> None:
    outcome = run()
    # 没有配置通道时 PUSH 阶段直接跳过，不产生推送状态记录；
    # 但清单里必须明确写 NOT_ATTEMPTED，而不是默认成功。
    assert outcome["push_status"] is None
    assert outcome["manifest"]["delivery_status"] == "NOT_ATTEMPTED"
    push_stage = next(stage for stage in outcome["manifest"]["stages"] if stage["stage"] == "PUSH")
    assert push_stage["status"] == "SKIPPED"


def test_push_failure_does_not_flip_run_to_failed(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    outcome = run(
        out_dir=tmp_path / "run",
        push_config={"delivery": {"channel": "file", "target": str(blocker / "sub")}},
    )

    assert outcome["manifest"]["delivery_status"] == "FAILED"
    assert outcome["manifest"]["run_status"] == "PARTIAL"
    assert outcome["manifest"]["counts"]["delivered"] == 0
    push_stage = next(stage for stage in outcome["manifest"]["stages"] if stage["stage"] == "PUSH")
    assert push_stage["status"] == "PARTIAL"
    # 选择与交付仍完整保留
    assert outcome["delivery"]["items"]


def test_push_brief_rejects_unknown_channel() -> None:
    brief = run()["brief"]
    config = PushConfig(channel="file", target="x")
    config.channel = "telepathy"
    try:
        push_brief(brief, config, "run-x", FIXED_NOW)
    except Exception as exc:  # noqa: BLE001
        assert "通道" in str(exc) or "channel" in str(exc).lower()
    else:
        raise AssertionError("未知通道必须报错")
