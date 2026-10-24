"""运行管理回归测试（WP-R7 / CAP-RUN-01..03）。

保护四件事：同一时间只能有一个运行；锁在失败后也必须释放；
阶段失败会阻断下游；检查点只在输入一致时复用。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.lock import LockHeld, RunLock
from runtime.run_manager import RunManager, StageSpec


def fixed_clock(iso: str = "2026-10-18T03:00:00+00:00"):
    moment = datetime.fromisoformat(iso)
    return lambda: moment


# ---------------------------------------------------------------- 锁


def test_lock_blocks_a_second_run(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    first = RunLock(path, "run-a", clock=fixed_clock())
    first.acquire()
    try:
        with pytest.raises(LockHeld) as excinfo:
            RunLock(path, "run-b", clock=fixed_clock()).acquire()
        assert excinfo.value.info["run_id"] == "run-a"
    finally:
        first.release()
    assert not path.exists()


def test_dead_pid_lock_is_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text(
        json.dumps(
            {
                "run_id": "run-crashed",
                "pid": 424242,
                "acquired_at": "2026-10-17T00:00:00+00:00",
                "host": "old-host",
            }
        ),
        encoding="utf-8",
    )
    info = RunLock(path, "run-new", clock=fixed_clock()).acquire()
    try:
        assert info.reclaimed is True
        assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "run-new"
    finally:
        info_lock = RunLock(path, "run-new", clock=fixed_clock())
        info_lock._info = info  # 直接接管，避免重复获取
        info_lock.release()


def test_lock_is_released_even_when_a_stage_fails(tmp_path: Path) -> None:
    def boom(context):
        raise RuntimeError("适配器炸了")

    manager = RunManager(tmp_path / "run.lock", "run-fail", clock=fixed_clock())
    outcome = manager.execute([StageSpec("SELECTION", boom)], {"k": 1})
    assert outcome.failed_stage == "SELECTION"
    assert not (tmp_path / "run.lock").exists()


def test_lock_records_owner(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    lock = RunLock(path, "run-owner", clock=fixed_clock())
    info = lock.acquire()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["run_id"] == "run-owner"
        assert stored["pid"] == info.pid
        assert stored["acquired_at"].startswith("2026-10-18T03:00:00")
    finally:
        lock.release()


# ---------------------------------------------------------------- 阶段与状态


def test_failed_stage_blocks_the_rest(tmp_path: Path) -> None:
    calls: list[str] = []

    def ok(context):
        calls.append("ok")
        return {"ok": True}

    def boom(context):
        calls.append("boom")
        raise RuntimeError("boom")

    stages = [
        StageSpec("PRECHECK", ok),
        StageSpec("SELECTION", boom),
        StageSpec("DELIVERY_BUILD", ok),
    ]
    outcome = RunManager(tmp_path / "run.lock", "run-x", clock=fixed_clock()).execute(stages, {"k": 1})

    assert [stage.status for stage in outcome.stages] == ["OK", "FAILED", "BLOCKED"]
    assert calls == ["ok", "boom"]
    assert outcome.run_status == "FAILED"
    failed = outcome.stages[1]
    assert failed.error and "boom" in failed.error
    assert failed.error_ref and "/" in failed.error_ref


def test_skipped_stage_has_no_timestamps(tmp_path: Path) -> None:
    outcome = RunManager(tmp_path / "run.lock", "run-x", clock=fixed_clock()).execute(
        [StageSpec("PUSH", skip=True)], {"k": 1}
    )
    assert outcome.stages[0].status == "SKIPPED"
    assert outcome.stages[0].started_at is None


def test_successful_run_status(tmp_path: Path) -> None:
    stages = [StageSpec("PRECHECK", lambda ctx: 1), StageSpec("PUSH", skip=True)]
    outcome = RunManager(tmp_path / "run.lock", "run-x", clock=fixed_clock()).execute(stages, {"k": 1})
    assert outcome.run_status == "SUCCESS"


# ---------------------------------------------------------------- 检查点


def test_checkpoint_reuses_only_matching_inputs(tmp_path: Path) -> None:
    counter = {"n": 0}

    def action(context):
        counter["n"] += 1
        return {"value": counter["n"]}

    manager = RunManager(
        tmp_path / "run.lock",
        "run-cp",
        clock=fixed_clock(),
        checkpoint_root=tmp_path / "checkpoints",
    )
    stages = [StageSpec("SELECTION", action, cacheable=True)]

    first = manager.execute(stages, {"input": "a"})
    second = manager.execute(stages, {"input": "a"})
    third = manager.execute(stages, {"input": "b"})

    assert counter["n"] == 2
    assert first.stages[0].reused is False
    assert second.stages[0].reused is True
    assert third.stages[0].reused is False
    assert second.payloads["SELECTION"] == {"value": 1}


def test_checkpoint_file_is_readable(tmp_path: Path) -> None:
    manager = RunManager(
        tmp_path / "run.lock",
        "run-cp",
        clock=fixed_clock(),
        checkpoint_root=tmp_path / "checkpoints",
    )
    manager.execute([StageSpec("SELECTION", lambda ctx: {"k": 1}, cacheable=True)], {"input": "a"})
    stored = json.loads((tmp_path / "checkpoints" / "SELECTION.json").read_text(encoding="utf-8"))
    assert stored["stage"] == "SELECTION"
    assert stored["payload"] == {"k": 1}
