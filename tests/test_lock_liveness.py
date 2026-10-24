"""锁存活性：被终止的进程不能继续占着锁。

真实事故：harness 杀掉一次运行后，锁文件里的 pid 仍能被 OpenProcess 打开
（句柄没释放），于是后续每一次运行都报"已有运行持有锁"，系统再也跑不起来。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from runtime.lock import RunLock, _pid_alive


def test_terminated_process_is_not_alive() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert _pid_alive(child.pid) is False


def test_lock_with_terminated_pid_is_reclaimed(tmp_path: Path) -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()

    lock_path = tmp_path / "run.lock"
    lock_path.write_text(
        json.dumps(
            {
                "run_id": "run-dead",
                "pid": child.pid,
                "acquired_at": "2026-10-18T13:20:00+08:00",
                "host": "test",
                "reclaimed": False,
            }
        ),
        encoding="utf-8",
    )

    lock = RunLock(lock_path, "run-new")
    info = lock.acquire()
    assert info.reclaimed is True
    assert info.run_id == "run-new"
    lock.release()
    assert not lock_path.exists()
