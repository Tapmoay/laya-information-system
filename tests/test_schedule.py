"""调度决策回归测试（WP-R7 / CAP-RUN-04）。

保护四件事：时区必须显式；错过计划时刻不补跑多轮；已跑过的时刻不重复跑；
超预算的运行要停下来并记为 ABORTED，而不是假装成功。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from runtime import scheduled_entry
from runtime.run_manager import RunManager, StageSpec
from runtime.schedule import (
    CronExpression,
    ScheduleConfig,
    ScheduleError,
    decide,
    validate_config,
)
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")


def config(**overrides) -> ScheduleConfig:
    base = {
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "calendar_rule": "0 9 * * *",
        "misfire_grace_seconds": 3600,
        "misfire_policy": "RUN_ONCE",
        "max_concurrent_runs": 1,
        "manual_trigger_enabled": True,
    }
    base.update(overrides)
    return ScheduleConfig.from_mapping(base)


def at(hour: int, minute: int = 0, day: int = 18) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


# ---------------------------------------------------------------- cron


def test_daily_rule_matches_and_rolls_back_a_day() -> None:
    cron = CronExpression.parse("0 9 * * *")
    assert cron.matches(at(9)) is True
    assert cron.matches(at(10)) is False
    assert cron.previous_at_or_before(at(12)) == at(9)
    assert cron.previous_at_or_before(at(8)) == at(9, day=17)


def test_next_occurrence_skips_to_tomorrow_after_time_has_passed() -> None:
    cron = CronExpression.parse("0 9 * * *")
    assert cron.next_after(at(9)) == at(9, day=19)
    assert cron.next_after(at(8)) == at(9)


def test_weekday_rule_only_fires_on_that_weekday() -> None:
    cron = CronExpression.parse("30 8 * * 1")  # 周一 08:30
    upcoming = cron.next_after(at(0))
    assert upcoming.isoweekday() == 1
    assert (upcoming.hour, upcoming.minute) == (8, 30)


def test_step_and_list_fields() -> None:
    cron = CronExpression.parse("*/15 9-10 1,15 * *")
    assert cron.matches(datetime(2026, 11, 1, 9, 0, tzinfo=TZ))
    assert cron.matches(datetime(2026, 11, 1, 10, 45, tzinfo=TZ))
    assert not cron.matches(datetime(2026, 11, 2, 9, 0, tzinfo=TZ))


# ---------------------------------------------------------------- 配置


def test_timezone_must_be_explicit() -> None:
    with pytest.raises(ScheduleError):
        ScheduleConfig.from_mapping({"enabled": True, "calendar_rule": "0 9 * * *"})


def test_invalid_timezone_is_reported() -> None:
    assert any("时区" in error for error in validate_config(config(timezone="Mars/Olympus")))


def test_invalid_rule_is_reported() -> None:
    errors = validate_config(config(calendar_rule="0 9 * *"))
    assert any("日历规则" in error for error in errors)


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ScheduleError):
        ScheduleConfig.from_mapping(
            {"enabled": False, "timezone": "UTC", "calendar_rule": "0 9 * * *", "surprise": 1}
        )


# ---------------------------------------------------------------- 决策


def test_disabled_schedule_never_runs() -> None:
    decision = decide(config(enabled=False), at(9))
    assert decision.action == "DISABLED"


def test_on_time_run() -> None:
    decision = decide(config(), at(9))
    assert decision.action == "RUN"
    assert decision.misfire is False
    assert decision.scheduled_for == at(9)


def test_already_ran_is_not_repeated() -> None:
    decision = decide(config(), at(12), last_run_at=at(9, 5))
    assert decision.action == "ALREADY_RUN"


def test_misfire_within_grace_still_runs() -> None:
    decision = decide(config(), at(9, 30))
    assert decision.action == "RUN"
    assert decision.misfire is False


def test_misfire_beyond_grace_skips_under_skip_policy() -> None:
    decision = decide(config(misfire_policy="SKIP"), at(12))
    assert decision.action == "MISFIRE_SKIPPED"
    assert decision.misfire is True
    assert decision.scheduled_for == at(9)


def test_misfire_beyond_grace_catches_up_once_under_run_once() -> None:
    decision = decide(config(misfire_policy="RUN_ONCE"), at(12))
    assert decision.action == "RUN"
    assert decision.misfire is True


# ---------------------------------------------------------------- 夏令时


def test_spring_forward_gap_runs_once() -> None:
    tz = ZoneInfo("America/New_York")
    cfg = config(timezone="America/New_York", calendar_rule="30 2 * * *")

    before = decide(cfg, datetime(2026, 3, 8, 3, 15, tzinfo=tz))
    assert before.scheduled_for == datetime(2026, 3, 7, 2, 30, tzinfo=tz)

    # 2026-03-08 的 02:30 不存在：时钟从 02:00 直接跳到 03:00
    at_gap = decide(cfg, datetime(2026, 3, 8, 3, 30, tzinfo=tz))
    assert at_gap.action == "RUN"
    assert at_gap.scheduled_for is not None

    later = decide(cfg, datetime(2026, 3, 8, 4, 30, tzinfo=tz), last_run_at=at_gap.scheduled_for)
    assert later.action == "ALREADY_RUN", "夏令时跳变当天不能跑两次"


def test_fall_back_hour_does_not_run_twice() -> None:
    tz = ZoneInfo("America/New_York")
    cfg = config(timezone="America/New_York", calendar_rule="30 1 * * *")

    first = decide(cfg, datetime(2026, 11, 1, 1, 45, tzinfo=tz))
    assert first.action == "RUN"

    # 01:30 在这一天出现两次；第二次不应该再触发一轮
    second = decide(cfg, datetime(2026, 11, 1, 2, 15, tzinfo=tz), last_run_at=first.scheduled_for)
    assert second.action == "ALREADY_RUN"


# ---------------------------------------------------------------- 记录与入口


def test_trigger_record_passes_contract() -> None:
    decision = decide(config(), at(9))
    record = scheduled_entry.build_trigger_record("trg-20261018090000", decision, config(), "run-x")
    assert validate("trigger-record", record) == []


def test_scheduled_entry_writes_trigger_and_runs(tmp_path: Path) -> None:
    config_path = tmp_path / "schedule.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schedule": {
                    "enabled": True,
                    "timezone": "Asia/Shanghai",
                    "calendar_rule": "0 9 * * *",
                    "misfire_grace_seconds": 3600,
                    "misfire_policy": "RUN_ONCE",
                    "max_concurrent_runs": 1,
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    code = scheduled_entry.main(
        [
            "--config", str(config_path),
            "--case", str(ROOT / "fixtures" / "m1" / "basic"),
            "--out", str(out_dir),
            "--now", "2026-10-18T09:00:00+08:00",
        ]
    )
    assert code == 0
    trigger = json.loads((out_dir / "trigger.json").read_text(encoding="utf-8"))
    assert trigger["action"] == "RUN"
    assert trigger["run_id"]
    assert validate("trigger-record", trigger) == []
    # 每轮产物写进各自目录，避免定时任务反复写同一个 --out 时互相覆盖
    manifests = list(out_dir.rglob("run-manifest.json"))
    assert len(manifests) == 1
    assert manifests[0].parent.name == trigger["run_id"]
    assert (out_dir / "triggers" / f"{trigger['trigger_id']}.json").exists()


def test_scheduled_entry_skips_when_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "schedule.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {"schedule": {"enabled": False, "timezone": "UTC", "calendar_rule": "0 9 * * *"}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    code = scheduled_entry.main(
        [
            "--config", str(config_path),
            "--case", str(ROOT / "fixtures" / "m1" / "basic"),
            "--out", str(out_dir),
            "--now", "2026-10-18T09:00:00+08:00",
        ]
    )
    assert code == 0
    trigger = json.loads((out_dir / "trigger.json").read_text(encoding="utf-8"))
    assert trigger["action"] == "DISABLED"
    assert not (out_dir / "run-manifest.json").exists()


# ---------------------------------------------------------------- 预算


class StepClock:
    def __init__(self, start: datetime, step_seconds: float = 1.0) -> None:
        self.current = start
        self.step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        value = self.current
        self.current = self.current + self.step
        return value


def test_budget_exhaustion_aborts_instead_of_lying(tmp_path: Path) -> None:
    clock = StepClock(datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc))
    manager = RunManager(
        tmp_path / "run.lock", "run-budget", clock=clock, max_duration_seconds=0
    )
    stages = [StageSpec("PRECHECK", lambda ctx: 1), StageSpec("SELECTION", lambda ctx: 2)]
    outcome = manager.execute(stages, {"k": 1})

    assert outcome.aborted is True
    assert [stage.status for stage in outcome.stages] == ["BLOCKED", "BLOCKED"]
    assert outcome.run_status == "ABORTED"
    assert not (tmp_path / "run.lock").exists()


def test_stage_can_check_its_own_budget(tmp_path: Path) -> None:
    from runtime.run_manager import BudgetExceeded

    def action(context):
        context.deadline = context.clock() - timedelta(seconds=1)
        context.check_budget()
        return 1

    manager = RunManager(tmp_path / "run.lock", "run-b", clock=lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    outcome = manager.execute([StageSpec("SELECTION", action)], {"k": 1})
    assert outcome.failed_stage == "SELECTION"
    assert "预算" in (outcome.error or "")
    assert BudgetExceeded
