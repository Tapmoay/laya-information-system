"""参数回路必须真的接在每日链路上，而不是"写好了但没人读"。

这条测试盯的是三个具体的接线点：

1. `agent_select.py` 读参数版本（权重 + 阈值），并把用了哪版写进运行产物；
2. `learn.py` 能直接吃 `selection.json` 的 `override_events`（以前只认 JSON 数组/JSONL）；
3. `backlog.py` 的候补门槛跟候选池是同一条线。

存在理由：docs/23 §9 记过一次同类缺口——调参能跑，但消费方没接，
于是"参数动了、结果一模一样"。这里让每个消费方都有用例。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.agent_select as agent_select
import tools.backlog as backlog
import tools.learn as learn
from judgment.params import PRIOR, ParameterStore, Parameters, project

ROOT = Path(__file__).resolve().parents[1]


def _scores(primary: float = 0.6, value: float = 0.8, quality: float = 0.9) -> dict:
    return {
        "primary": {"noul": primary},
        "secondary": {"noul": 0.1},
        "exploration": {"noul": 0.1},
        "task": {"noul": 0.1},
        "value_task": {"noul": value},
        "value_capability": {"noul": value * 0.9},
        "value_decision": {"noul": value * 0.8},
        "value_exploration": {"noul": value * 0.7},
        "value_environment": {"noul": value * 0.6},
        "quality_testable": {"noul": quality},
        "quality_increment": {"noul": quality * 0.9},
        "form": {"probabilities": {"TECHNICAL": 0.9, "PRODUCT": 0.03, "EVENT": 0.02,
                                   "RECRUIT": 0.03, "MARKETING": 0.02}},
        "recruit": {"noul": 0.1},
    }


def _write_batch(tmp_path: Path, rows: list[tuple[str, dict]]) -> tuple[Path, Path]:
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
        {"item_id": item_id, "profile_id": "ai", "scores": payload,
         "provenance": {"title": f"标题 {index}", "domain": "ai"}}
        for index, (item_id, payload) in enumerate(rows, 1)
    ]
    items_path = tmp_path / "items.json"
    items_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    scores_path = tmp_path / "scores.jsonl"
    scores_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in scores), encoding="utf-8"
    )
    return items_path, scores_path


def _demand(tmp_path: Path) -> Path:
    path = tmp_path / "demand.json"
    path.write_text(
        json.dumps(
            {
                "active_goals": [{"statement": "示例主方向"}],
                "active_tasks": [{"statement": "把参数回路接进每日链路"}],
                "exploration_intent": {"enabled": True, "domains_hint": ["结构在别处的化身"]},
                "constraints": {"daily_information_budget_minutes": 30},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _store_with(root: Path, user: str, **overrides) -> ParameterStore:
    store = ParameterStore(root, user)
    parameters = project({**PRIOR, **overrides})
    from judgment.params import ParameterVersion

    store.write(ParameterVersion(1, parameters, "2026-10-22T00:00:00+08:00", "TEST", 0, "测试用"))
    return store


def test_agent_select_records_which_parameter_version_it_used(tmp_path: Path) -> None:
    items_path, scores_path = _write_batch(tmp_path, [("item-1", _scores())])
    store = _store_with(tmp_path / "params", "u1", threshold=0.62)
    out = tmp_path / "agent"
    code = agent_select.main([
        "--build", "--scores", str(scores_path), "--items", str(items_path),
        "--demand", str(_demand(tmp_path)), "--out", str(out),
        "--params", str(tmp_path / "params"), "--user", "u1",
    ])
    assert code == 0
    audit = json.loads((out / "params.json").read_text(encoding="utf-8"))
    assert audit["version"] == 1
    assert audit["threshold"] == pytest.approx(0.62)
    assert audit["threshold_from"] == "params"
    assert audit["digest"] == store.current().parameters.digest()


def test_explicit_coarse_threshold_wins_and_says_so(tmp_path: Path) -> None:
    items_path, scores_path = _write_batch(tmp_path, [("item-1", _scores())])
    _store_with(tmp_path / "params", "u1", threshold=0.62)
    out = tmp_path / "agent"
    agent_select.main([
        "--build", "--scores", str(scores_path), "--items", str(items_path),
        "--demand", str(_demand(tmp_path)), "--out", str(out),
        "--params", str(tmp_path / "params"), "--user", "u1", "--coarse", "0.44",
    ])
    audit = json.loads((out / "params.json").read_text(encoding="utf-8"))
    assert audit["threshold"] == pytest.approx(0.44)
    assert audit["threshold_from"] == "cli"


def test_scoring_weights_come_from_the_parameter_version(tmp_path: Path) -> None:
    """同一批测量，换权重必须换分数——否则参数只是被读了，没被用。"""
    items_path, scores_path = _write_batch(
        tmp_path, [("item-1", _scores(primary=0.4, value=0.5, quality=0.9))]
    )
    quality_heavy = project({**PRIOR, "direction": 0.10, "value": 0.25, "quality": 0.55, "exploration": 0.10})
    direction_heavy = project({**PRIOR, "direction": 0.55, "value": 0.25, "quality": 0.10, "exploration": 0.10})

    def score_with(parameters: Parameters) -> float:
        rows = agent_select._candidates(
            scores_path, items_path, limit=5, threshold=0.0, weights=parameters.to_weights()
        )
        return rows[0]["score"]

    assert score_with(quality_heavy) > score_with(direction_heavy)


def test_threshold_decides_whether_a_candidate_is_marked_selected(tmp_path: Path) -> None:
    items_path, scores_path = _write_batch(tmp_path, [("item-1", _scores())])
    strict = agent_select._candidates(scores_path, items_path, limit=5, threshold=0.99)
    loose = agent_select._candidates(scores_path, items_path, limit=5, threshold=0.40)
    assert strict[0]["selected"] is False
    assert loose[0]["selected"] is True


def test_learn_reads_override_events_straight_from_selection_json(tmp_path: Path) -> None:
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "selection": {"core": [], "exploration": [], "excluded": []},
                "override_events": [
                    {"item_id": "item-1", "include": False, "was_selected": True, "reason": "广告"},
                    {"item_id": "item-2", "include": True, "was_selected": False, "reason": "漏了"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = learn._read_actions(selection)
    assert [row["item_id"] for row in rows] == ["item-1", "item-2"]


def test_learn_still_reads_arrays_and_single_line_jsonl(tmp_path: Path) -> None:
    array = tmp_path / "a.json"
    array.write_text(json.dumps([{"item_id": "x", "include": True}]), encoding="utf-8")
    assert learn._read_actions(array) == [{"item_id": "x", "include": True}]

    one_line = tmp_path / "a.jsonl"
    one_line.write_text(json.dumps({"item_id": "y", "include": False}), encoding="utf-8")
    assert learn._read_actions(one_line) == [{"item_id": "y", "include": False}]


def test_daily_loop_commits_and_the_next_build_sees_it(tmp_path: Path) -> None:
    """一整圈：取舍 → 新版本 → 次日的 --build 用新版本。"""
    items_path, scores_path = _write_batch(
        tmp_path,
        [("item-1", _scores()), ("item-2", _scores()), ("item-3", _scores()), ("item-4", _scores())],
    )
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "override_events": [
                    {"item_id": "item-1", "include": False, "was_selected": True, "reason": "广告"},
                    {"item_id": "item-2", "include": False, "was_selected": True, "reason": "广告"},
                    {"item_id": "item-4", "include": True, "was_selected": False, "reason": "有用"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code = learn.main([
        "--user", "u1", "--base", str(tmp_path / "params"),
        "--scores", str(scores_path), "--from-overrides", str(selection), "--apply",
    ])
    assert code == 0
    current = ParameterStore(tmp_path / "params", "u1").current()
    assert current.version == 1

    out = tmp_path / "agent"
    agent_select.main([
        "--build", "--scores", str(scores_path), "--items", str(items_path),
        "--demand", str(_demand(tmp_path)), "--out", str(out),
        "--params", str(tmp_path / "params"), "--user", "u1",
    ])
    audit = json.loads((out / "params.json").read_text(encoding="utf-8"))
    assert audit["version"] == 1
    assert audit["parameters"] == current.parameters.as_dict()


def test_backlog_uses_the_same_line_as_the_candidate_pool(tmp_path: Path) -> None:
    items_path, _ = _write_batch(tmp_path, [("item-1", _scores())])
    scores_path = tmp_path / "scored.jsonl"
    scores_path.write_text(
        json.dumps({"item_id": "item-1", "scores": _scores(), "score": 0.60}, ensure_ascii=False) + chr(10),
        encoding="utf-8",
    )
    _store_with(tmp_path / "params", "u1", threshold=0.90)
    ledger = tmp_path / "backlog.jsonl"
    backlog.main([
        "--carry", "--items", str(items_path), "--scores", str(scores_path),
        "--ledger", str(ledger), "--params", str(tmp_path / "params"), "--user", "u1",
    ])
    assert not ledger.exists() or ledger.read_text(encoding="utf-8").strip() == ""

    _store_with(tmp_path / "params", "u1", threshold=0.40)
    backlog.main([
        "--carry", "--items", str(items_path), "--scores", str(scores_path),
        "--ledger", str(ledger), "--params", str(tmp_path / "params"), "--user", "u1",
    ])
    assert "item-1" in ledger.read_text(encoding="utf-8")


def test_daily_runbook_contains_the_feedback_step() -> None:
    text = (ROOT / "config" / "daily-runbook.md").read_text(encoding="utf-8")
    assert "tools/learn.py" in text
    assert "--from-overrides" in text
    assert "selection.json" in text
