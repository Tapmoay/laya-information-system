"""调度决策（CAP-RUN-04）。

只负责回答一个问题：**现在该不该跑**。真正的定时器由操作系统提供
（Windows 任务计划 / systemd timer / cron），本模块不常驻、不轮询。

设计约束：
- 时区必须显式配置，不允许依赖主机隐式时区（v0.3 §2.2）。
- 错过计划时刻不补跑多轮：由 misfire_policy 决定只跑一次还是跳过。
- 决策本身要留下可审计记录（TriggerRecord）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

MISFIRE_POLICIES = ("RUN_ONCE", "SKIP")
MAX_SEARCH_DAYS = 366 * 5


class ScheduleError(ValueError):
    """配置或日历规则不合法。"""


def _aware_utc(moment: datetime) -> datetime:
    """统一换算到 UTC 再比。

    同一个 ZoneInfo 的两个 datetime 直接比较时，Python 会忽略 tzinfo、只比墙上时间。
    夏令时跳变当天，墙上 02:30 可能比 03:15 更晚（UTC 07:30 vs 07:15），
    直接比会把未来当成过去。
    """
    return moment.astimezone(timezone.utc)


def _parse_field(spec: str, low: int, high: int, label: str) -> frozenset[int]:
    values: set[int] = set()
    for part in spec.split(","):
        if not part:
            raise ScheduleError(f"{label} 字段为空")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ScheduleError(f"{label} 步长不是整数：{step_text}") from exc
            if step <= 0:
                raise ScheduleError(f"{label} 步长必须为正")
        if part == "*":
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ScheduleError(f"{label} 区间不合法：{part}") from exc
        else:
            try:
                start = end = int(part)
            except ValueError as exc:
                raise ScheduleError(f"{label} 取值不合法：{part}") from exc
        if start < low or end > high or start > end:
            raise ScheduleError(f"{label} 超出范围 {low}-{high}：{part}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ScheduleError(f"{label} 没有任何取值")
    return frozenset(values)


@dataclass(frozen=True)
class CronExpression:
    source: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        fields = expression.split()
        if len(fields) != 5:
            raise ScheduleError("日历规则必须是 5 段（分 时 日 月 周）")
        minutes = _parse_field(fields[0], 0, 59, "分")
        hours = _parse_field(fields[1], 0, 23, "时")
        days = _parse_field(fields[2], 1, 31, "日")
        months = _parse_field(fields[3], 1, 12, "月")
        weekdays = frozenset(0 if value == 7 else value for value in _parse_field(fields[4], 0, 7, "周"))
        return cls(
            source=expression,
            minutes=minutes,
            hours=hours,
            days=days,
            months=months,
            weekdays=weekdays,
            dom_restricted=fields[2] != "*",
            dow_restricted=fields[4] != "*",
        )

    # ------------------------------------------------------------------ 匹配

    def day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        dow_ok = (day.isoweekday() % 7) in self.weekdays  # isoweekday: 周一=1 … 周日=7 → 0
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def matches(self, moment: datetime) -> bool:
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and self.day_matches(moment.date())
        )

    # ------------------------------------------------------------------ 搜索

    def previous_at_or_before(self, moment: datetime) -> datetime | None:
        cursor = moment.replace(second=0, microsecond=0)
        day = cursor.date()
        for _ in range(MAX_SEARCH_DAYS):
            if day.month in self.months and self.day_matches(day):
                for hour in sorted(self.hours, reverse=True):
                    for minute in sorted(self.minutes, reverse=True):
                        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=cursor.tzinfo)
                        # 必须按 UTC 比：两个 datetime 共用同一个 tzinfo 时，Python 会忽略时区、
                        # 直接比墙上时间。夏令时跳变那天，这会把"还没到"的时刻当成"已经过了"。
                        if _aware_utc(candidate) <= _aware_utc(cursor):
                            return candidate
            day -= timedelta(days=1)
        return None

    def next_after(self, moment: datetime) -> datetime | None:
        cursor = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = cursor.date()
        for _ in range(MAX_SEARCH_DAYS):
            if day.month in self.months and self.day_matches(day):
                for hour in sorted(self.hours):
                    for minute in sorted(self.minutes):
                        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=cursor.tzinfo)
                        if _aware_utc(candidate) >= _aware_utc(cursor):
                            return candidate
            day += timedelta(days=1)
        return None


@dataclass
class ScheduleConfig:
    enabled: bool
    timezone: str
    calendar_rule: str
    misfire_grace_seconds: int = 3600
    misfire_policy: str = "SKIP"
    max_concurrent_runs: int = 1
    manual_trigger_enabled: bool = True

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ScheduleConfig":
        allowed = {
            "enabled", "timezone", "calendar_rule", "misfire_grace_seconds",
            "misfire_policy", "max_concurrent_runs", "manual_trigger_enabled",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ScheduleError(f"未知的调度配置项：{sorted(unknown)}")
        for key in ("enabled", "timezone", "calendar_rule"):
            if key not in data:
                raise ScheduleError(f"缺少必填项 {key}（时区必须显式配置）")
        return cls(
            enabled=bool(data["enabled"]),
            timezone=str(data["timezone"]),
            calendar_rule=str(data["calendar_rule"]),
            misfire_grace_seconds=int(data.get("misfire_grace_seconds", 3600)),
            misfire_policy=str(data.get("misfire_policy", "SKIP")),
            max_concurrent_runs=int(data.get("max_concurrent_runs", 1)),
            manual_trigger_enabled=bool(data.get("manual_trigger_enabled", True)),
        )


def load_config(path) -> ScheduleConfig:
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    if "schedule" in data:
        data = data["schedule"]
    return ScheduleConfig.from_mapping(data)


def validate_config(config: ScheduleConfig) -> list[str]:
    errors: list[str] = []
    if not config.timezone or config.timezone == "CONFIGURE_EXPLICITLY":
        errors.append("timezone 必须显式配置")
    else:
        try:
            ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"无法识别的时区：{config.timezone}")
    try:
        CronExpression.parse(config.calendar_rule)
    except ScheduleError as exc:
        errors.append(f"日历规则不合法：{exc}")
    if config.misfire_policy not in MISFIRE_POLICIES:
        errors.append(f"misfire_policy 必须是 {MISFIRE_POLICIES}")
    if config.misfire_grace_seconds < 0:
        errors.append("misfire_grace_seconds 不能为负")
    if config.max_concurrent_runs != 1:
        errors.append("max_concurrent_runs 必须为 1（单实例运行）")
    return errors


@dataclass
class TriggerDecision:
    action: str
    decided_at: datetime
    scheduled_for: datetime | None
    misfire: bool
    reason: str


def decide(config: ScheduleConfig, now: datetime, last_run_at: datetime | None = None) -> TriggerDecision:
    if now.tzinfo is None:
        raise ScheduleError("now 必须带时区")
    if not config.enabled:
        return TriggerDecision("DISABLED", now, None, False, "调度未启用")

    errors = validate_config(config)
    if errors:
        raise ScheduleError("；".join(errors))

    tz = ZoneInfo(config.timezone)
    local_now = now.astimezone(tz)
    cron = CronExpression.parse(config.calendar_rule)
    scheduled = cron.previous_at_or_before(local_now)
    if scheduled is None:
        return TriggerDecision("SKIP", now, None, False, "日历规则在可搜索范围内没有匹配时刻")

    if last_run_at is not None:
        if last_run_at.tzinfo is None:
            raise ScheduleError("last_run_at 必须带时区")
        if _aware_utc(last_run_at) >= _aware_utc(scheduled):
            return TriggerDecision(
                "ALREADY_RUN", now, scheduled, False,
                f"该计划时刻（{scheduled.isoformat()}）已经运行过",
            )

    lag_seconds = (local_now - scheduled).total_seconds()
    misfire = lag_seconds > config.misfire_grace_seconds
    if misfire and config.misfire_policy == "SKIP":
        return TriggerDecision(
            "MISFIRE_SKIPPED", now, scheduled, True,
            f"错过计划时刻 {int(lag_seconds)} 秒，超过宽限 {config.misfire_grace_seconds} 秒，按策略跳过",
        )

    reason = "到达计划时刻" if not misfire else f"补跑一次（已错过 {int(lag_seconds)} 秒）"
    return TriggerDecision("RUN", now, scheduled, misfire, reason)
