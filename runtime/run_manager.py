"""运行管理器：锁 → 阶段执行 → 检查点 → 清单回调 → 释放锁。

M1 只覆盖离线链路需要的部分：单实例锁、阶段失败即停、可复用检查点、
失败也要留下清单。调度、超时、并发控制属于 M2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from runtime.checkpoints import CheckpointStore, canonical_hash
from runtime.lock import LockInfo, RunLock
from runtime.state import derive_run_status


@dataclass
class StageSpec:
    name: str
    action: Callable[["RunContext"], Any] | None = None
    cacheable: bool = False
    skip: bool = False
    # 阶段失败（抛异常）时是否降级为 PARTIAL 并继续，而不是中止整轮。
    # 典型用途：推送失败不应让已经完成的选择与交付白跑。
    partial_on_error: bool = False
    # 由返回载荷决定阶段状态，例如推送返回 FAILED 时阶段记 PARTIAL。
    status_from_payload: Callable[[Any], str] | None = None


class BudgetExceeded(RuntimeError):
    """本轮时间预算已耗尽。"""


@dataclass
class RunContext:
    run_id: str
    payloads: dict[str, Any] = field(default_factory=dict)
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    deadline: datetime | None = None

    def get(self, stage: str) -> Any:
        return self.payloads[stage]

    def check_budget(self) -> None:
        """阶段内部可主动检查预算；这是协作式超时，不是强制中断。"""
        if self.deadline is not None and self.clock() > self.deadline:
            raise BudgetExceeded("超出本轮时间预算")


@dataclass
class StageOutcome:
    name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    error_ref: str | None = None
    reused: bool = False

    def as_manifest(self) -> dict[str, Any]:
        """error_ref 是引用，不是正文：自由文本进 errors.json，清单里只留指针
        （docs/03 §6 的序列化要求）。"""
        return {
            "stage": self.name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_ref": self.error_ref,
        }


@dataclass
class RunOutcome:
    run_id: str
    stages: list[StageOutcome]
    payloads: dict[str, Any]
    failed_stage: str | None = None
    error: str | None = None
    lock: LockInfo | None = None
    aborted: bool = False

    @property
    def run_status(self) -> str:
        return derive_run_status((stage.status for stage in self.stages), aborted=self.aborted)


class RunManager:
    def __init__(
        self,
        lock_path: Path,
        run_id: str,
        clock: Callable[[], datetime] | None = None,
        checkpoint_root: Path | None = None,
        stale_after_seconds: int = 6 * 3600,
        max_duration_seconds: float | None = None,
    ) -> None:
        self.run_id = run_id
        self.max_duration_seconds = max_duration_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = RunLock(lock_path, run_id, stale_after_seconds=stale_after_seconds, clock=self._clock)
        self._checkpoints = CheckpointStore(checkpoint_root) if checkpoint_root else None

    def execute(
        self,
        stages: list[StageSpec],
        context_key: Any,
        on_complete: Callable[[RunOutcome], None] | None = None,
    ) -> RunOutcome:
        lock_info = self._lock.acquire()
        run_started = self._clock()
        deadline = (
            run_started + timedelta(seconds=self.max_duration_seconds)
            if self.max_duration_seconds is not None
            else None
        )
        context = RunContext(run_id=self.run_id, clock=self._clock, deadline=deadline)
        outcome = RunOutcome(run_id=self.run_id, stages=[], payloads=context.payloads, lock=lock_info)

        try:
            for index, spec in enumerate(stages):
                if deadline is not None and self._clock() > deadline:
                    outcome.aborted = True
                    outcome.error = "超出本轮时间预算"
                    for blocked in stages[index:]:
                        outcome.stages.append(StageOutcome(blocked.name, "BLOCKED"))
                    break
                if spec.skip:
                    outcome.stages.append(StageOutcome(spec.name, "SKIPPED"))
                    continue

                started = self._clock()
                try:
                    payload, reused = self._run_stage(spec, context, context_key, started)
                except Exception as exc:  # noqa: BLE001 - 阶段失败必须被记录
                    finished = self._clock()
                    error_text = f"{type(exc).__name__}: {exc}"
                    error_ref = f"trace/{self.run_id}/{spec.name.lower()}/error"
                    if spec.partial_on_error:
                        context.payloads[spec.name] = {"error": str(exc)}
                        outcome.stages.append(
                            StageOutcome(
                                name=spec.name,
                                status="PARTIAL",
                                started_at=started.isoformat(timespec="seconds"),
                                finished_at=finished.isoformat(timespec="seconds"),
                                error=error_text,
                                error_ref=error_ref,
                            )
                        )
                        continue

                    outcome.stages.append(
                        StageOutcome(
                            name=spec.name,
                            status="FAILED",
                            started_at=started.isoformat(timespec="seconds"),
                            finished_at=finished.isoformat(timespec="seconds"),
                            error=error_text,
                            error_ref=error_ref,
                        )
                    )
                    outcome.failed_stage = spec.name
                    outcome.error = str(exc)
                    for blocked in stages[index + 1:]:
                        outcome.stages.append(StageOutcome(blocked.name, "BLOCKED"))
                    break

                status = "OK"
                if spec.status_from_payload is not None:
                    status = spec.status_from_payload(payload)
                context.payloads[spec.name] = payload
                outcome.stages.append(
                    StageOutcome(
                        name=spec.name,
                        status=status,
                        started_at=started.isoformat(timespec="seconds"),
                        finished_at=self._clock().isoformat(timespec="seconds"),
                        reused=reused,
                    )
                )
        finally:
            try:
                if on_complete is not None:
                    on_complete(outcome)
            finally:
                self._lock.release()

        return outcome

    def _run_stage(
        self,
        spec: StageSpec,
        context: RunContext,
        context_key: Any,
        started: datetime,
    ) -> tuple[Any, bool]:
        key = canonical_hash({"stage": spec.name, "context": context_key})
        if spec.cacheable and self._checkpoints is not None:
            cached = self._checkpoints.load(spec.name, key)
            if cached is not None:
                return cached, True

        if spec.action is None:
            raise ValueError(f"阶段 {spec.name} 没有 action")

        payload = spec.action(context)
        if spec.cacheable and self._checkpoints is not None:
            self._checkpoints.save(spec.name, key, payload, started)
        return payload, False
