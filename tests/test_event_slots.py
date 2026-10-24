"""候选池：最多 30 条，其中最多 3 条「事件候选」不按分数进。

为什么要有这几个用例：

1. 重大事件可能和主目标毫无关系，分数落在门槛以下——但它不能被"分低"挡在终选之外，
   否则推送里永远只有"我正在做的事"，对世界的了解只会越来越窄；
2. 事件位不能成为降权项的后门：营销、招聘、产品通稿不应借"环境价值高"重新进来；
3. 总数仍是 30，事件位只是名额的一种分配，不是额外扩容。
"""

from __future__ import annotations

import json
from pathlib import Path

import tools.agent_select as agent_select


def _scores(*, direction: float = 0.6, value: float = 0.8, quality: float = 0.9, env: float = 0.3,
            marketing: float = 0.02) -> dict:
    return {
        "primary": {"noul": direction},
        "secondary": {"noul": 0.1},
        "exploration": {"noul": 0.1},
        "task": {"noul": 0.1},
        "value_task": {"noul": value},
        "value_capability": {"noul": value * 0.9},
        "value_decision": {"noul": value * 0.8},
        "value_exploration": {"noul": value * 0.7},
        "value_environment": {"noul": env},
        "quality_testable": {"noul": quality},
        "quality_increment": {"noul": quality * 0.9},
        "form": {"probabilities": {"TECHNICAL": 0.9 - marketing, "PRODUCT": marketing,
                                   "EVENT": 0.01, "RECRUIT": 0.01, "MARKETING": marketing}},
        "recruit": {"noul": 0.1},
    }


def _write(tmp_path: Path, rows: list[tuple[str, dict]]) -> tuple[Path, Path]:
    items = [
        {
            "item_id": item_id,
            "title": f"标题 {index}",
            "text_clean": "这是一段足够长的正文。" * 40,
            "canonical_url": f"https://example.com/{item_id}",
            "provenance": {"title": f"标题 {index}", "domain": "ai"},
        }
        for index, (item_id, _) in enumerate(rows, 1)
    ]
    scores = [
        {"item_id": item_id, "scores": payload, "provenance": {"title": f"标题 {index}"}}
        for index, (item_id, payload) in enumerate(rows, 1)
    ]
    items_path = tmp_path / "items.json"
    items_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    scores_path = tmp_path / "scores.jsonl"
    scores_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in scores), encoding="utf-8"
    )
    return items_path, scores_path


def test_event_candidate_enters_pool_even_below_the_score_threshold(tmp_path: Path) -> None:
    items_path, scores_path = _write(
        tmp_path,
        [
            ("item-high", _scores()),
            # 和主目标基本无关、质量也低，但宣布了可能改变世界的事
            ("item-event", _scores(direction=0.05, value=0.1, quality=0.1, env=0.8)),
        ],
    )
    rows = agent_select._candidates(scores_path, items_path, limit=30, threshold=0.45)
    ids = [row["item_id"] for row in rows]
    assert "item-event" in ids
    event = next(row for row in rows if row["item_id"] == "item-event")
    assert event["event_slot"] is True
    assert event["score"] < 0.45  # 它确实是靠事件位进来的，不是靠分


def test_reduced_items_cannot_take_the_event_slot(tmp_path: Path) -> None:
    items_path, scores_path = _write(
        tmp_path,
        [
            ("item-high", _scores()),
            ("item-ad", _scores(direction=0.05, value=0.1, quality=0.1, env=0.8, marketing=0.9)),
        ],
    )
    rows = agent_select._candidates(scores_path, items_path, limit=30, threshold=0.45)
    assert all(row["item_id"] != "item-ad" for row in rows)


def test_pool_never_exceeds_the_limit_and_events_come_out_of_it(tmp_path: Path) -> None:
    rows_in = [(f"item-{i:02d}", _scores()) for i in range(40)]
    rows_in.append(("item-event-1", _scores(direction=0.05, value=0.1, quality=0.1, env=0.9)))
    rows_in.append(("item-event-2", _scores(direction=0.05, value=0.1, quality=0.1, env=0.8)))
    items_path, scores_path = _write(tmp_path, rows_in)

    rows = agent_select._candidates(scores_path, items_path, limit=30, threshold=0.45)
    ids = [row["item_id"] for row in rows]
    assert len(rows) == 30
    assert len(set(ids)) == 30
    assert {"item-event-1", "item-event-2"} <= set(ids)
    assert sum(1 for row in rows if row.get("event_slot")) == 2


def test_no_event_slots_when_nothing_clears_the_environment_floor(tmp_path: Path) -> None:
    items_path, scores_path = _write(tmp_path, [("item-1", _scores(env=0.5)), ("item-2", _scores(env=0.3))])
    rows = agent_select._candidates(scores_path, items_path, limit=30, threshold=0.45)
    assert all(not row.get("event_slot") for row in rows)
    assert len(rows) == 2


def test_runbook_has_no_sampling_and_uses_a_thirty_item_pool() -> None:
    text = (Path(__file__).resolve().parents[1] / "config" / "daily-runbook.md").read_text(encoding="utf-8")
    assert "按来源轮转抽样" not in text
    assert "--per-source 4" not in text
    assert "--max-total 300" in text
    assert "--limit 30" in text
    assert "事件候选" in text
    assert "major_event" in text


# ----------------------------------------------------------------- 事件日志


CANDIDATES = [
    {"item_id": "item-a", "title": "甲", "url": "https://a", "score": 0.8, "values": {"value_environment": 0.2}},
    {"item_id": "item-b", "title": "乙", "url": "https://b", "score": 0.0, "values": {"value_environment": 0.81},
     "event_slot": True},
]


def test_event_log_records_candidates_and_major_decisions(tmp_path: Path) -> None:
    final = {
        "core": [{"item_id": "item-a", "reason": "推进任务", "score": 0.8, "major_event": True,
                  "event_reason": "改变了可做的事"}],
        "exploration": [],
        "excluded": [],
    }
    path = tmp_path / "event-log.jsonl"
    agent_select._write_event_log(path, CANDIDATES, final, run_date="2026-10-22", params_version=1)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {row["item_id"]: row for row in rows}
    assert by_id["item-b"]["event_candidate"] is True and by_id["item-b"]["picked"] is False
    assert by_id["item-a"]["major"] is True
    assert by_id["item-a"]["event_reason"] == "改变了可做的事"
    assert by_id["item-a"]["params_version"] == 1


def test_event_log_is_rewritten_per_day_not_appended(tmp_path: Path) -> None:
    path = tmp_path / "event-log.jsonl"
    final = {"core": [], "exploration": [], "excluded": []}
    agent_select._write_event_log(path, CANDIDATES, final, run_date="2026-10-22", params_version=1)
    agent_select._write_event_log(path, CANDIDATES, final, run_date="2026-10-22", params_version=2)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1  # 只有事件候选，没有重大判定
    assert rows[0]["params_version"] == 2  # 同一天重跑以最后一次为准


def test_major_event_without_a_reason_is_rejected() -> None:
    from judgment.agent_select import apply_decision

    summaries = {"item-a": "一段摘要，里面有原文可以摘。"}
    decision = {"picks": [{"item_id": "item-a", "reason": "重要", "evidence": "里面有原文可以摘",
                           "major_event": True}]}
    check = apply_decision(CANDIDATES, decision, summaries=summaries)
    assert not check.ok
    assert any("重大事件" in problem for problem in check.violations)


def test_events_review_summarises_the_log(tmp_path: Path, capsys) -> None:
    import tools.events_review as review

    path = tmp_path / "event-log.jsonl"
    final = {"core": [{"item_id": "item-a", "reason": "推进", "score": 0.8, "major_event": True,
                       "event_reason": "边界移动了"}], "exploration": [], "excluded": []}
    agent_select._write_event_log(path, CANDIDATES, final, run_date="2026-10-22", params_version=1)
    assert review.main(["--log", str(path)]) == 0
    out = capsys.readouterr().out
    assert "2026-10-22" in out
    assert "改变了什么：边界移动了" in out
    assert "命中率" in out


def test_apply_writes_the_event_log_end_to_end(tmp_path: Path) -> None:
    items_path, scores_path = _write(
        tmp_path,
        [
            ("item-1", _scores()),
            ("item-2", _scores(direction=0.05, value=0.1, quality=0.1, env=0.8)),
        ],
    )
    out = tmp_path / "agent"
    out.mkdir()
    decision = {
        "picks": [{
            "item_id": "item-1", "slot": "core", "reason": "推进当前任务",
            "evidence": "这是一段足够长的正文", "major_event": True,
            "event_reason": "它移动了可做之事的边界",
        }],
        "excludes": [],
    }
    (out / "decision.json").write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")
    code = agent_select.main([
        "--apply", "--scores", str(scores_path), "--items", str(items_path),
        "--out", str(out), "--params", str(tmp_path / "params"), "--user", "u1",
        "--date", "2026-10-22", "--event-log", str(tmp_path / "event-log.jsonl"),
    ])
    assert code == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "event-log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_id = {row["item_id"]: row for row in rows}
    assert by_id["item-1"]["major"] is True
    assert by_id["item-2"]["event_candidate"] is True
