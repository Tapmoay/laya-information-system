"""云端判断的三条契约：提示词覆盖 13 问、坏输出会被修、失败不许伪装成 0 分。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from judgment import cloud_judge as cj
from judgment.questions import build_questions
from tools import score_items


def _specs() -> list:
    demand = {
        "active_goals": [{"statement": "示例主方向"}],
        "active_tasks": [{"statement": "示例任务"}],
        "exploration_intent": {"statement": "跨领域线索"},
    }
    return build_questions(demand)


def _noul(specs: list) -> list[str]:
    return [spec.name for spec in specs if spec.kind == "noul"]


def _payload(specs: list, value: float = 0.42) -> dict:
    return {
        "scores": {name: value for name in _noul(specs)},
        "form": {label: 1.0 for label in cj.FORM_LABELS},
        "note": "样本不足",
    }


def _post(content: str, usage: dict | None = None):
    def fake(messages, **kwargs):
        return content, (usage or {"prompt_tokens": 10, "completion_tokens": 2})

    return fake


def test_prompt_has_every_question_and_forbids_deciding() -> None:
    specs = _specs()
    messages = cj.build_messages(specs, "标题", "正文" * 100)
    text = "\n".join(message["content"] for message in messages)
    for name in _noul(specs):
        assert name in text
    for label in cj.FORM_LABELS:
        assert label in text
    assert "不要评价" in messages[0]["content"]
    assert "不要替用户做取舍" in messages[0]["content"]


def test_parse_normalizes_form_weights_and_accepts_nested_scores() -> None:
    specs = _specs()
    payload = {"questions": _payload(specs)["scores"], "form": {
        "technical": 2, "product": 1, "event": 0, "recruit": 0, "marketing": 0,
    }}
    scores, note = cj.parse_output(json.dumps(payload), specs)
    assert scores["primary"] == {"noul": 0.42}
    assert scores["form"]["probabilities"] == {
        "TECHNICAL": 0.6667, "PRODUCT": 0.3333, "EVENT": 0.0, "RECRUIT": 0.0, "MARKETING": 0.0,
    }
    assert note == ""


def test_parse_rejects_out_of_range_with_a_stable_code() -> None:
    specs = _specs()
    payload = _payload(specs)
    payload["scores"]["primary"] = 42
    with pytest.raises(cj.CloudJudgeError) as caught:
        cj.parse_output(json.dumps(payload), specs)
    assert caught.value.code == "OUT_OF_RANGE"


def test_parse_rejects_missing_question_instead_of_defaulting_to_zero() -> None:
    specs = _specs()
    payload = _payload(specs)
    payload["scores"].pop("value_decision")
    with pytest.raises(cj.CloudJudgeError) as caught:
        cj.parse_output(json.dumps(payload), specs)
    assert caught.value.code == "MISSING_KEYS"
    assert "value_decision" in caught.value.detail


def test_parse_accepts_a_fenced_json_reply() -> None:
    specs = _specs()
    fenced = "```json\n" + json.dumps(_payload(specs)) + "\n```"
    scores, _ = cj.parse_output(fenced, specs)
    assert scores["task"]["noul"] == 0.42


def test_bad_output_is_sent_back_for_repair_then_accepted(monkeypatch) -> None:
    specs = _specs()
    calls: list[list[dict[str, str]]] = []

    def fake_post(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"scores": {"primary": 0.5}}), {"prompt_tokens": 5, "completion_tokens": 1}
        return json.dumps(_payload(specs)), {"prompt_tokens": 8, "completion_tokens": 2}

    monkeypatch.setattr(cj, "_post", fake_post)
    output = cj.judge(specs, "标题", "正文" * 100, api_key="test", retries=2)
    assert output.attempts == 2
    assert output.scores["primary"]["noul"] == 0.42
    assert "不合法" in calls[1][-1]["content"]


def test_auth_errors_are_not_retried(monkeypatch) -> None:
    specs = _specs()
    calls = {"n": 0}

    def fake_post(messages, **kwargs):
        calls["n"] += 1
        raise cj.CloudJudgeError("HTTP_401", "invalid key")

    monkeypatch.setattr(cj, "_post", fake_post)
    with pytest.raises(cj.CloudJudgeError) as caught:
        cj.judge(specs, "标题", "正文" * 100, api_key="bad", retries=3)
    assert caught.value.code == "HTTP_401"
    assert calls["n"] == 1


def test_gives_up_after_retries_and_raises(monkeypatch) -> None:
    specs = _specs()
    calls = {"n": 0}

    def fake_post(messages, **kwargs):
        calls["n"] += 1
        return "not json", {}

    monkeypatch.setattr(cj, "_post", fake_post)
    with pytest.raises(cj.CloudJudgeError) as caught:
        cj.judge(specs, "标题", "正文" * 100, api_key="test", retries=1)
    assert caught.value.code == "BAD_JSON"
    assert calls["n"] == 2


def test_batch_keeps_failures_explicit_and_order_stable(monkeypatch) -> None:
    specs = _specs()
    items = [{"item_id": "a", "title": "A"}, {"item_id": "b", "title": "B"}]
    prepared = {"a": {"text": "正文" * 100}, "b": {"text": "正文" * 100}}

    def fake_judge(specs_arg, title, text, **kwargs):
        if title == "B":
            raise cj.CloudJudgeError("NETWORK", "timeout")
        return cj.JudgeOutput(
            scores={"primary": {"noul": 0.5}, "form": {"probabilities": {label: 0.2 for label in cj.FORM_LABELS}}},
            note="",
            usage={"prompt_tokens": 3, "completion_tokens": 1},
            attempts=1,
            ms=10,
        )

    monkeypatch.setattr(cj, "judge", fake_judge)
    results = cj.measure_batch(items, specs, prepared, api_key="test", workers=2)
    assert [row["item_id"] for row in results] == ["a", "b"]
    assert "scores" in results[0]
    assert results[1]["error"]["code"] == "NETWORK"
    assert "scores" not in results[1]


# ---------------------------------------------------------------- score_items 集成


def _write_demand(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "active_goals": [{"statement": "AI 系统"}],
                "active_tasks": [{"statement": "agent"}],
                "exploration_intent": {"statement": "结构"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _write_items(path: Path) -> Path:
    rows = [
        {"item_id": "item-1", "title": "A", "text_clean": "正文内容。" * 80,
         "canonical_url": "https://example.com/1", "source_id": "SRC-X",
         "provenance": {"http_status": 200}},
        {"item_id": "item-2", "title": "B", "text_clean": "",
         "canonical_url": "https://example.com/2", "source_id": "SRC-X"},
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def _scores_for(specs: list) -> dict:
    return {
        **{name: {"noul": 0.5} for name in _noul(specs)},
        "form": {"probabilities": {label: 0.2 for label in cj.FORM_LABELS}},
    }


def test_score_items_skips_unscorable_and_writes_cloud_rows(tmp_path: Path, monkeypatch) -> None:
    specs = _specs()
    items = _write_items(tmp_path / "items.json")
    demand = _write_demand(tmp_path / "demand.json")
    seen: list[list[str]] = []

    def fake_batch(measurable, specs_arg, prepared, **kwargs):
        seen.append([item["item_id"] for item in measurable])
        return [
            {"item_id": item["item_id"], "scores": _scores_for(specs),
             "usage": {"prompt_tokens": 10, "completion_tokens": 2},
             "attempts": 1, "ms": 12, "note": ""}
            for item in measurable
        ]

    monkeypatch.setattr(cj, "measure_batch", fake_batch)
    monkeypatch.setattr(cj, "resolve_api_key", lambda: "test-key")
    code = score_items.main([
        "--items", str(items), "--demand", str(demand), "--profile-id", "daily", "--provider", "cloud",
        "--out", str(tmp_path / "scores.jsonl"), "--workers", "1",
    ])
    assert code == 0
    assert seen == [["item-1"]]
    rows = [json.loads(line) for line in (tmp_path / "scores.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["judge"]["provider"] == "cloud"
    assert set(rows[0]["scores"]) == set(_noul(specs)) | {"form"}
    unscorable = json.loads((tmp_path / "unscorable.json").read_text(encoding="utf-8"))
    assert [row["error"] for row in unscorable["items"]] == ["EMPTY_TEXT"]


def test_score_items_fails_loudly_when_coverage_drops(tmp_path: Path, monkeypatch) -> None:
    items = _write_items(tmp_path / "items.json")
    demand = _write_demand(tmp_path / "demand.json")

    def fake_batch(measurable, specs_arg, prepared, **kwargs):
        return [{"item_id": item["item_id"], "error": {"code": "NETWORK", "detail": "timeout"}}
                for item in measurable]

    monkeypatch.setattr(cj, "measure_batch", fake_batch)
    monkeypatch.setattr(cj, "resolve_api_key", lambda: "test-key")
    code = score_items.main([
        "--items", str(items), "--demand", str(demand), "--profile-id", "daily", "--provider", "cloud",
        "--out", str(tmp_path / "scores.jsonl"), "--workers", "1",
    ])
    assert code == 1
    failures = [json.loads(line) for line in
                (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["error"]["code"] for row in failures] == ["NETWORK"]


def test_score_items_dry_run_never_calls_the_api(tmp_path: Path, monkeypatch) -> None:
    items = _write_items(tmp_path / "items.json")
    demand = _write_demand(tmp_path / "demand.json")

    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应该调用 API")

    monkeypatch.setattr(cj, "measure_batch", boom)
    monkeypatch.setattr(cj, "resolve_api_key", boom)
    code = score_items.main([
        "--items", str(items), "--demand", str(demand), "--profile-id", "daily", "--provider", "cloud",
        "--out", str(tmp_path / "scores.jsonl"), "--dry-run",
    ])
    assert code == 0

