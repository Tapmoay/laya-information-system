"""重复问题检测回归测试（CAP-FBK-04）。

保护三件事：窗口不足不说话、证据必须可点、观察不等于改法。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from feedback.detector import Observation, detect_observations, load_feedback, load_manifests
from runtime.offline_run import run_case
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-10-18T04:50:00+08:00")


def manifest(run_id: str, *, discovery: str = "OK", selected: int = 1, delivered: int = 1, candidates: int = 3) -> dict:
    return {
        "run_id": run_id,
        "started_at": "2026-10-18T03:00:00+08:00",
        "stages": [{"stage": "DISCOVERY", "status": discovery}],
        "counts": {"candidates": candidates, "measurements": candidates, "selected": selected, "delivered": delivered},
    }


def codes(observations: list[Observation]) -> set[str]:
    return {item.code for item in observations}


# ---------------------------------------------------------------- 阈值


def test_no_observation_below_threshold() -> None:
    manifests = [
        manifest("run-1", discovery="PARTIAL"),
        manifest("run-2", discovery="PARTIAL"),
        manifest("run-3"),
        manifest("run-4"),
        manifest("run-5"),
    ]
    assert "SOURCE_UNSTABLE" not in codes(detect_observations(manifests))


def test_source_unstable_detected_with_evidence() -> None:
    manifests = [manifest(f"run-{i}", discovery="PARTIAL") for i in range(1, 4)] + [
        manifest("run-4"),
        manifest("run-5"),
    ]
    observations = detect_observations(manifests)
    unstable = next(item for item in observations if item.code == "SOURCE_UNSTABLE")
    assert len(unstable.evidence_refs) == 3
    assert all(ref.endswith("run-manifest.json") for ref in unstable.evidence_refs)


def test_empty_delivery_streak_detected() -> None:
    manifests = [manifest(f"run-{i}", selected=0, delivered=0) for i in range(1, 4)]
    assert "EMPTY_DELIVERY_STREAK" in codes(detect_observations(manifests))


def test_window_ignores_old_history() -> None:
    old = [manifest(f"old-{i}", discovery="PARTIAL") for i in range(1, 4)]
    recent = [manifest(f"new-{i}") for i in range(1, 6)]
    assert "SOURCE_UNSTABLE" not in codes(detect_observations(old + recent, window=5))


# ---------------------------------------------------------------- 反馈缺口


def test_feedback_gap_detected() -> None:
    manifests = [manifest(f"run-{i}") for i in range(1, 4)]
    assert "FEEDBACK_GAP" in codes(detect_observations(manifests, []))


def test_feedback_gap_silenced_by_user_statement() -> None:
    manifests = [manifest(f"run-{i}") for i in range(1, 4)]
    feedback = [{"feedback_id": "fb-1", "kind": "USER_STATED", "candidate_id": "cand-1"}]
    assert "FEEDBACK_GAP" not in codes(detect_observations(manifests, feedback))


def test_observed_actions_do_not_close_the_gap() -> None:
    manifests = [manifest(f"run-{i}") for i in range(1, 4)]
    feedback = [{"feedback_id": "fb-2", "kind": "OBSERVED_ACTION", "candidate_id": "cand-1"}]
    assert "FEEDBACK_GAP" in codes(detect_observations(manifests, feedback))


# ---------------------------------------------------------------- 契约与边界


def test_observations_satisfy_contract() -> None:
    manifests = [manifest(f"run-{i}", discovery="PARTIAL", selected=0, delivered=0) for i in range(1, 4)]
    for item in detect_observations(manifests):
        payload = item.as_dict()
        assert validate("observation", payload) == []
        assert "changes" not in payload and "policy" not in payload, "观察不能夹带改法"


def test_duplicate_run_ids_collapse_in_evidence() -> None:
    manifests = [manifest("run-x", discovery="PARTIAL") for _ in range(3)]
    observation = next(
        item for item in detect_observations(manifests) if item.code == "SOURCE_UNSTABLE"
    )
    assert observation.evidence_refs == ["runs/run-x/run-manifest.json"]
    assert validate("observation", observation.as_dict()) == []


def test_empty_input_yields_nothing() -> None:
    assert detect_observations([]) == []


def test_load_manifests_from_run_directory(tmp_path: Path) -> None:
    for index in range(3):
        run_case(
            ROOT / "fixtures" / "m1" / "empty",
            out_dir=tmp_path / f"run-{index}",
            run_id=f"run-detect-{index}",
            now=NOW,
        )
    manifests = load_manifests(tmp_path)
    assert len(manifests) == 3
    assert "EMPTY_DELIVERY_STREAK" in codes(detect_observations(manifests))


def test_load_feedback_reads_jsonl(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "feedback.jsonl").write_text(
        '{"_id": "fb-1", "kind": "USER_STATED"}\n{"_id": "fb-2", "kind": "OBSERVED_ACTION"}\n',
        encoding="utf-8",
    )
    assert len(load_feedback(store)) == 2
