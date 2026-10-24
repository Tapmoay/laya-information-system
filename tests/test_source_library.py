"""信息源库与任务池的一致性：这两个文件分叉时不会报错，只会让打分悄悄偏移。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest.source_index import SourceIndexError, load
from judgment.questions import slot_values

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "sources-index.yaml"
POOL = ROOT / "demand" / "task-pool.json"

import sys  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.source_index import DOC, INDEX_JSON, machine_index, render  # noqa: E402


def _data():
    return load(REGISTRY)


def _pool():
    return json.loads(POOL.read_text(encoding="utf-8"))


def _latest_demand():
    paths = sorted((ROOT / "demand").glob("demand-2*.json"))
    return json.loads(paths[-1].read_text(encoding="utf-8"))


def test_registry_has_every_facet_and_valid_serves():
    data = _data()
    assert data["sources"], "登记表不能为空"
    targets = {item["id"] for group in data["targets"].values() for item in group}
    for source in data["sources"]:
        assert source.field and source.kind and source.role
        assert source.language in ("zh-Hans", "en")
        assert source.serves, source.id
        assert set(source.serves) <= targets, source.id


def test_every_goal_and_task_is_fed_by_at_least_one_source():
    """覆盖为空的目标不是"以后再说"，是当场就没人喂它。"""
    data = _data()
    starved = [
        item["id"]
        for group in data["targets"].values()
        for item in group
        if not any(item["id"] in source.serves for source in data["sources"])
    ]
    assert not starved, starved


def test_registry_rejects_unknown_field(tmp_path):
    text = REGISTRY.read_text(encoding="utf-8").replace("    field: ai\n", "    field: cooking\n", 1)
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SourceIndexError):
        load(path)


def test_doc_and_json_index_are_not_stale():
    """手写清单一定会和登记表漂移；这里让漂移变成红灯，而不是等某天发现。"""
    data = _data()
    assert DOC.read_text(encoding="utf-8") == render(data), "docs/26 过期，请跑 tools/source_index.py --render"
    assert json.loads(INDEX_JSON.read_text(encoding="utf-8")) == machine_index(data), "sources.index.json 过期"


def test_task_pool_has_exactly_one_enabled_task():
    pool = _pool()
    enabled = [item for item in pool["candidates"] if item.get("state") == "ENABLED"]
    assert len(enabled) == 1, enabled


def test_enabled_task_matches_demand_task_slot():
    """池子说 A、快照说 B，这个分叉过去要靠人去发现。"""
    active = [item for item in _pool()["candidates"] if item.get("state") == "ENABLED"][0]
    demand = _latest_demand()
    live = [item for item in demand["active_tasks"] if item["status"] == "ACTIVE"]
    assert len(live) == 1, live
    assert live[0]["task_id"] == active["task_id"]
    assert slot_values(demand)["task"] == active["statement"]


def test_task_pool_candidates_point_at_registered_goals():
    data = _data()
    known = {item["id"] for group in data["targets"].values() for item in group}
    for item in _pool()["candidates"]:
        assert item["covers_goal"] in known, item["task_id"]
        assert item["task_id"] in known, item["task_id"]


def test_query_filters_by_facets():
    from tools.source_index import query

    data = _data()
    preprints = query(data["sources"], ["kind=preprint"])
    assert preprints and all(item.kind == "preprint" for item in preprints)
    control = query(data["sources"], ["role=control"])
    assert control and all(item.role == "control" for item in control)
