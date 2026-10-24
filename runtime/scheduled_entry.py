"""调度入口（CAP-RUN-04 的可执行部分）。

由操作系统定时器调用：每次只判断"该不该跑"，该跑就跑一轮然后退出。
本模块不常驻、不轮询。

用法：
    python -m runtime.scheduled_entry --config config/schedule.example.yaml \
        --case fixtures/m1/basic --out runs/scheduled --now 2026-10-18T09:00:00+08:00
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime.offline_run import run_case
from runtime.schedule import ScheduleError, decide, load_config, validate_config
from tools.validate_objects import validate


def last_successful_run(runs_root: Path | None) -> datetime | None:
    """从运行产物里找最近一次成功/部分成功的运行时间。

    生产环境的调度器每 N 分钟被唤醒一次，没人会手动传 --last-run；
    所以"上一次跑过没有"必须从产物里读，这和 checkpoints、manifest 是同一套事实。
    """
    if runs_root is None or not runs_root.exists():
        return None
    latest: datetime | None = None
    for path in runs_root.rglob("run-manifest.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(manifest["started_at"])
        except (OSError, ValueError, KeyError):
            continue
        if manifest.get("run_status") not in {"SUCCESS", "PARTIAL"}:
            continue
        if latest is None or started > latest:
            latest = started
    return latest


def build_trigger_record(trigger_id: str, decision, config, run_id: str | None) -> dict:
    return {
        "trigger_id": trigger_id,
        "decided_at": decision.decided_at.isoformat(timespec="seconds"),
        "action": decision.action,
        "scheduled_for": decision.scheduled_for.isoformat(timespec="seconds") if decision.scheduled_for else None,
        "timezone": config.timezone,
        "calendar_rule": config.calendar_rule,
        "misfire": decision.misfire,
        "misfire_policy": config.misfire_policy,
        "reason": decision.reason,
        "run_id": run_id,
        "contract_version": "1.0",
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="调度决策入口")
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--case", default=None, help="offline 模式必填")
    parser.add_argument("--sources", default=None, help="live 模式必填：来源配置 YAML")
    parser.add_argument("--out", default=None)
    parser.add_argument("--now", default=None, help="ISO 时间；缺省用当前时刻")
    parser.add_argument("--last-run", default=None, help="上一次成功运行时间（ISO）")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (ScheduleError, OSError) as exc:
        print(f"配置读取失败：{exc}")
        return 2

    errors = validate_config(config)
    if errors:
        print("调度配置不合法：")
        for item in errors:
            print(f"  - {item}")
        return 2

    zone = ZoneInfo(config.timezone)
    now = datetime.fromisoformat(args.now).astimezone(zone) if args.now else datetime.now(zone)
    last_run = None
    last_run_source = "none"
    if args.last_run:
        last_run = datetime.fromisoformat(args.last_run)
        last_run_source = "argument"
    else:
        last_run = last_successful_run(Path(args.out) if args.out else None)
        if last_run is not None:
            last_run_source = "manifest"

    try:
        decision = decide(config, now, last_run)
    except ScheduleError as exc:
        print(f"调度决策失败：{exc}")
        return 2

    run_id: str | None = None
    exit_code = 0
    if decision.action == "RUN" and not args.out:
        # 没有产物目录就没有"上次运行"的记忆，重复运行保护会失效
        print("调度运行必须指定 --out：否则无法从产物恢复上次运行时间，会重复执行")
        return 2
    if decision.action == "RUN":
        run_id = args.run_id or f"run-sched-{now:%Y%m%d%H%M}"
        # 每轮单独一个目录：定时任务会反复写同一个 --out，扁平布局会把上一轮覆盖掉
        run_out = str(Path(args.out) / run_id) if args.out else None
        try:
            if args.mode == "live":
                if not args.sources:
                    print("live 模式需要 --sources")
                    return 2
                from runtime.live_config import load_live_config
                from runtime.live_run import run_live

                live_config = load_live_config(args.sources)
                result = run_live(
                    live_config, run_id=run_id, out_dir=run_out, now=now, trigger="SCHEDULE"
                )
            else:
                if not args.case:
                    print("offline 模式需要 --case")
                    return 2
                result = run_case(args.case, out_dir=run_out, run_id=run_id, now=now, trigger="SCHEDULE")
        except (ValueError, OSError) as exc:
            print(f"运行配置不合法：{exc}")
            return 2

        failed = result["failed_stage"]
        print(
            f"run_id={run_id} mode={args.mode} run_status={result['manifest'].get('run_status')} "
            f"selected={result['manifest'].get('counts', {}).get('selected', 0)}"
        )
        if failed:
            print(f"运行在 {failed} 阶段失败：{result['error']}")
            exit_code = 1

    record = build_trigger_record(f"trg-{now:%Y%m%d%H%M%S}", decision, config, run_id)
    problems = validate("trigger-record", record)
    if problems:
        print("调度记录违反契约：" + "; ".join(problems))
        return 3

    out_path = Path(args.out) if args.out else None
    if out_path is not None:
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "trigger.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        history = out_path / "triggers"
        history.mkdir(parents=True, exist_ok=True)
        (history / f"{record['trigger_id']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"action={decision.action} scheduled_for={record['scheduled_for']} misfire={decision.misfire}")
    print(f"reason={decision.reason}")
    print(f"last_run={last_run.isoformat(timespec='seconds') if last_run else None}（来源：{last_run_source}）")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
