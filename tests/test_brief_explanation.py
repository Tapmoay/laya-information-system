"""简报里的"为什么"：只翻译已记录的测量，不新增判断。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from delivery.renderer import VALUE_LABELS, render_brief
from runtime.offline_run import load_case, run_case

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "fixtures" / "m1" / "basic"
NOW = datetime.fromisoformat("2026-10-18T06:00:00+08:00")


def test_brief_explains_why_in_human_terms(tmp_path: Path) -> None:
    outcome = run_case(CASE, out_dir=tmp_path / "run", run_id="run-why-01", now=NOW)
    brief = outcome["brief"]
    first = brief["items"][0]
    assert first["explanation"], "有测量时应当能说出为什么"
    assert any("任务价值" in line for line in first["explanation"])
    assert any("直接相关" in line for line in first["explanation"])


def test_explanation_only_uses_recorded_labels(tmp_path: Path) -> None:
    outcome = run_case(CASE, out_dir=tmp_path / "run", run_id="run-why-02", now=NOW)
    decisions = {item["candidate_id"]: item for item in outcome["decisions"]}
    measurements = {item["measurement_id"]: item for item in load_case(CASE).measurements}

    for item in outcome["brief"]["items"]:
        decision = decisions[item["candidate_id"]]
        allowed = set()
        for component in decision["priority_components"]:
            for ref in component["source_measurement_refs"]:
                value = measurements[ref].get("value") or {}
                if value.get("label"):
                    allowed.add(VALUE_LABELS.get(value["label"], value["label"]))
        for line in item["explanation"]:
            assert any(label in line for label in allowed), line


def test_render_without_decisions_keeps_empty_explanation() -> None:
    delivery = {
        "delivery_id": "del-x",
        "built_at": NOW.isoformat(timespec="seconds"),
        "selection_refs": [],
        "items": [
            {
                "candidate_id": "cand-1",
                "priority_rank": 1,
                "grouping": "CORE",
                "source_url": "https://example.org/a",
                "original_title": {"text": "Title", "language": "en"},
                "rendered_title": None,
                "delivery_language_mode": "ZH_TRANSLATION",
                "rationale_refs": [],
                "required_caveats": [],
                "recommended_action": "STUDY",
            }
        ],
        "delivery_language_mode": "ZH_TRANSLATION",
        "empty_reason": None,
        "contract_version": "1.0",
    }
    brief = render_brief(delivery, NOW)
    assert brief["items"][0]["explanation"] == []
