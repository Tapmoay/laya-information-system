"""单实例锁。

为什么不用内存锁：进程会退出，锁必须留下痕迹，才能在下次启动时判断
"上次是正常结束，还是崩在半路"。所以用文件 + pid + 时间戳。
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class LockHeld(RuntimeError):
    def __init__(self, info: dict) -> None:
        self.info = info
        super().__init__(
            f"已有运行持有锁：run_id={info.get('run_id')} pid={info.get('pid')} "
            f"acquired_at={info.get('acquired_at')}"
        )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            # 进程被终止后，只要还有句柄引用，OpenProcess 仍会成功。
            # 所以必须再看退出码：STILL_ACTIVE(259) 才是真的在跑。
            code = wintypes.DWORD(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class LockInfo:
    run_id: str
    pid: int
    acquired_at: str
    host: str
    reclaimed: bool = False


class RunLock:
    def __init__(
        self,
        path: Path,
        run_id: str,
        stale_after_seconds: int = 6 * 3600,
        clock=None,
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.stale_after_seconds = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._info: LockInfo | None = None

    # ------------------------------------------------------------------ 内部

    def _stale(self, info: dict) -> tuple[bool, str]:
        pid = int(info.get("pid") or -1)
        if not _pid_alive(pid):
            return True, f"pid {pid} 已不存在"
        acquired_at = info.get("acquired_at")
        if acquired_at:
            try:
                age = (self._clock() - datetime.fromisoformat(acquired_at)).total_seconds()
            except ValueError:
                return True, "acquired_at 无法解析"
            if age > self.stale_after_seconds:
                return True, f"锁已存在 {int(age)} 秒，超过上限"
        return False, ""

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ 接口

    def acquire(self) -> LockInfo:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reclaimed = False

        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                existing = self._read()
                stale, reason = self._stale(existing)
                if attempt == 0 and stale:
                    self.path.unlink(missing_ok=True)
                    reclaimed = True
                    continue
                raise LockHeld(existing) from None
            else:
                info = LockInfo(
                    run_id=self.run_id,
                    pid=os.getpid(),
                    acquired_at=self._clock().isoformat(),
                    host=socket.gethostname(),
                    reclaimed=reclaimed,
                )
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(info.__dict__, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._info = info
                return info

        raise LockHeld(self._read())

    def release(self) -> None:
        if self._info is None:
            return
        current = self._read()
        if current.get("run_id") == self._info.run_id and current.get("pid") == self._info.pid:
            self.path.unlink(missing_ok=True)
        self._info = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
