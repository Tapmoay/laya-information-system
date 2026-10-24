"""先验 + 在线后训练：边界、单步上限、回放护栏、版本与回滚。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from judgment.adaptive import (
    MAX_FLIP_RATIO,
    MIN_EVENTS,
    FeedbackEvent,
    aggregate_pulls,
    append_pending,
    commit,
    load_pending,
    propose,
    record_ledger,
    replay_guard,
    rollback,
)
from judgment.params import BOUNDS, MAX_STEP, PRIOR, ParameterStore, Parameters, apply_steps, project

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)


def _event(kind: str, *, signal=None, at=None, summary=None, delivered=True, item_id="item-x") -> FeedbackEvent:
    return FeedbackEvent(
        event_id=f"{kind}-{signal or item_id}",
        at=(at or NOW).isoformat(timespec="seconds"),
        kind=kind,
        signal=signal,
        item_id=item_id if kind != "STATEMENT" else None,
        statement="太窄了，老是同类" if kind == "STATEMENT" else None,
        summary=summary,
        delivered=delivered,
    )


def _summary(direction=0.5, policy=0.5, quality=0.5, exploration=0.0, reduced=False) -> dict:
    return {
        "direction_max": direction,
        "policy_max": policy,
        "quality": quality,
        "exploration": exploration,
        "reduced": reduced,
        "score": 0.5,
        "form": "TECHNICAL",
    }


# --------------------------------------------------------------------- 参数形状


def test_projection_clamps_and_normalises():
    params = project(
        {"direction": 0.9, "value": 0.05, "quality": 0.9, "exploration": 0.9,
         "reduced": 2.0, "direction_floor": -1.0, "threshold": 5.0}
    )
    total = params.direction + params.value + params.quality + params.exploration
    assert total == pytest.approx(1.0, abs=1e-6)
    for name in ("direction", "value", "quality", "exploration"):
        low, high = BOUNDS[name]
        assert low - 1e-9 <= getattr(params, name) <= high + 1e-9
    assert params.reduced == BOUNDS["reduced"][1]
    assert params.direction_floor == BOUNDS["direction_floor"][0]
    assert params.threshold == BOUNDS["threshold"][1]


def test_prior_weights_sum_to_one():
    prior = project(PRIOR)
    assert prior.direction + prior.value + prior.quality + prior.exploration == pytest.approx(1.0, abs=1e-9)


def test_single_step_is_capped():
    current = project(PRIOR)
    desired = dict(current.as_dict())
    desired["quality"] = 0.9
    desired["threshold"] = 0.1
    moved_params, moved = apply_steps(current, desired)
    assert moved["quality"] == pytest.approx(MAX_STEP["quality"], abs=1e-6)
    assert moved["threshold"] == pytest.approx(-MAX_STEP["threshold"], abs=1e-6)
    assert moved_params.quality <= BOUNDS["quality"][1]


def test_digest_changes_with_parameters():
    assert project(PRIOR).digest() != project({**PRIOR, "threshold": PRIOR["threshold"] + 0.01}).digest()


# --------------------------------------------------------------------- 归因


def test_veto_pushes_weights_away_from_the_vetoed_features():
    params = project(PRIOR)
    pulls = aggregate_pulls([_event("ITEM_VETO", summary=_summary(quality=0.9, direction=0.1, policy=0.1))],
                            params, now=NOW)
    # 一次性否决是"给这条降分"：每个维度都按自己的特征大小被扣，
    # 相对扣得最多的是质量维，而不是被归到一个"最大贡献项"上
    assert pulls["quality"] < 0
    assert pulls["quality"] < pulls["direction"]


def test_contrastive_events_separate_on_quality_only():
    """一条质量高、一条质量低：用户要低质量和不要高质量时，质量权重必须下调。"""
    params = project(PRIOR)
    veto = _event("ITEM_VETO", summary=_summary(quality=0.9, direction=0.5, policy=0.5), item_id="v")
    promote = _event("ITEM_PROMOTE", summary=_summary(quality=0.2, direction=0.5, policy=0.5), item_id="p")
    pulls = aggregate_pulls([veto, promote], params, now=NOW)
    assert pulls["quality"] < 0


def test_veto_on_reduced_item_also_raises_penalty():
    pulls = aggregate_pulls([_event("ITEM_VETO", summary=_summary(reduced=True))], project(PRIOR), now=NOW)
    assert pulls.get("reduced", 0) > 0


def test_promote_on_undelivered_item_lowers_threshold():
    summary = _summary(direction=0.1, policy=0.1, quality=0.1)
    pulls = aggregate_pulls(
        [_event("ITEM_PROMOTE", delivered=False, summary=summary)], project(PRIOR), now=NOW
    )
    assert pulls["threshold"] < 0


def test_statement_pulls_match_signal():
    params = project(PRIOR)
    assert aggregate_pulls([_event("STATEMENT", signal="TOO_NARROW")], params, now=NOW)["exploration"] > 0
    assert aggregate_pulls([_event("STATEMENT", signal="AD_LIKE")], params, now=NOW)["reduced"] > 0
    assert aggregate_pulls([_event("STATEMENT", signal=None)], params, now=NOW) == {}


# --------------------------------------------------------------------- 门槛


def test_too_few_events_changes_nothing(tmp_path):
    store = ParameterStore(tmp_path, "u1")
    events = [_event("STATEMENT", signal="TOO_NARROW")] * (MIN_EVENTS - 1)
    proposal = propose(store, events, now=NOW)
    assert proposal.accepted is False
    assert "不足" in proposal.reason
    assert proposal.after.as_dict() == store.current().parameters.as_dict()


def test_enough_events_produce_a_bounded_change(tmp_path):
    store = ParameterStore(tmp_path, "u1")
    events = [_event("STATEMENT", signal="TOO_NARROW")] * MIN_EVENTS
    proposal = propose(store, events, now=NOW)
    assert proposal.accepted is True
    before = store.current().parameters
    total = sum(proposal.after.as_dict()[name] for name in ("direction", "value", "quality", "exploration"))
    assert total == pytest.approx(1.0, abs=1e-6)
    assert proposal.after.exploration > before.exploration
    assert proposal.after.direction < before.direction


def test_old_events_count_less():
    store_events = [
        _event("STATEMENT", signal="TOO_NARROW", at=NOW - timedelta(days=days)) for days in (0, 400)
    ]
    from judgment.adaptive import event_weight

    fresh, stale = (event_weight(event, NOW) for event in store_events)
    assert fresh > stale * 100


# --------------------------------------------------------------------- 回放护栏


def test_replay_guard_blocks_bulk_flip():
    before = project(PRIOR)
    after = project({**PRIOR, "threshold": 0.90})
    ledger = [
        {"direction_max": 0.674, "policy_max": 0.674, "quality": 0.674, "exploration": 0.0,
         "reduced": False, "delivered": True}
        for _ in range(24)
    ]
    guard = replay_guard(before, after, ledger)
    assert guard["ok"] is False
    assert guard["ratio"] > MAX_FLIP_RATIO


def test_replay_guard_skips_small_history():
    guard = replay_guard(project(PRIOR), project({**PRIOR, "threshold": 0.7}), [{"delivered": True}] * 3)
    assert guard["ok"] is None or guard.get("skipped")


def test_propose_respects_guard(tmp_path):
    store = ParameterStore(tmp_path, "u1")
    # 分数要刚好卡在当前阈值（0.40）之上、单步上调（0.04）之后之下，才谈得上"被推翻"
    rows = [
        {"direction_max": 0.4565, "policy_max": 0.4565, "quality": 0.4565, "exploration": 0.0,
         "reduced": False, "delivered": True}
    ] * 24
    record_ledger(store, rows)
    from judgment.adaptive import load_ledger

    events = [_event("STATEMENT", signal="TOO_MUCH")] * MIN_EVENTS
    proposal = propose(store, events, ledger=load_ledger(store), now=NOW)
    assert proposal.accepted is False
    assert "护栏" in proposal.reason


# --------------------------------------------------------------------- 版本


def test_commit_creates_version_and_clears_pending(tmp_path):
    store = ParameterStore(tmp_path, "u1")
    events = [_event("STATEMENT", signal="TOO_SHALLOW", at=NOW) for _ in range(MIN_EVENTS)]
    append_pending(store, events)
    proposal = propose(store, load_pending(store), now=NOW)
    assert proposal.accepted
    record = commit(store, proposal, now=NOW)
    assert record.version == 1
    assert load_pending(store) == []
    assert store.current().version == 1
    assert len(store.history()) >= 2


def test_rollback_restores_prior_as_new_version(tmp_path):
    store = ParameterStore(tmp_path, "u1")
    events = [_event("STATEMENT", signal="TOO_SHALLOW", at=NOW) for _ in range(MIN_EVENTS)]
    proposal = propose(store, events, now=NOW)
    commit(store, proposal, now=NOW)
    assert store.current().parameters != project(PRIOR)

    record = rollback(store, 0, now=NOW)
    assert record.version == 2
    assert record.parameters.as_dict() == project(PRIOR).as_dict()
    assert store.current().source == "ROLLBACK"


def test_pending_queue_is_not_double_counted(tmp_path):
    store = ParameterStore(tmp_path, "u1")
    append_pending(store, [_event("STATEMENT", signal="TOO_NARROW", at=NOW)])
    assert len(load_pending(store)) == 1
    append_pending(store, [_event("STATEMENT", signal="TOO_NARROW", at=NOW)])
    assert len(load_pending(store)) == 2


def test_events_are_appended_to_audit_log(tmp_path):
    store = ParameterStore(tmp_path, "u1")
    events = [_event("STATEMENT", signal="TOO_SHALLOW", at=NOW) for _ in range(MIN_EVENTS)]
    proposal = propose(store, events, now=NOW)
    commit(store, proposal, now=NOW)
    log = (tmp_path / "u1" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == MIN_EVENTS
    assert json.loads(log[0])["applied_version"] == 1
