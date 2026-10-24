"""调度重复运行防护：第二次唤醒必须知道上一次已经跑过。

这条测试模拟真实调度器的行为：同一个 --out 目录被反复调用，
没有人工传 --last-run，系统必须自己从产物里读出来。
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest
import yaml

from runtime import scheduled_entry
from runtime.scheduled_entry import last_successful_run

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = ROOT / "fixtures" / "html"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HTML_DIR), **kwargs)

    def log_message(self, *args):
        return


@pytest.fixture(scope="module")
def base_url():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def write_configs(tmp_path: Path, base_url: str) -> tuple[Path, Path]:
    sources = {
        "sources": {
            "allowed_hosts": ["127.0.0.1"],
            "timeout_seconds": 5,
            "max_attempts": 1,
            "retry_backoff_seconds": 0.01,
            "urls": [f"{base_url}/article.html"],
        },
        "run": {
            "demand_path": "fixtures/m1/basic/demand-snapshot.json",
            "policy_config_path": "fixtures/m1/basic/policy-config.json",
            "model_profile_id": "MODEL-PROFILE-STUB-AGENT",
            "allow_unvalidated": True,
        },
    }
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.safe_dump(sources, allow_unicode=True), encoding="utf-8")

    schedule = {
        "schedule": {
            "enabled": True,
            "timezone": "Asia/Shanghai",
            "calendar_rule": "0 9 * * *",
            "misfire_grace_seconds": 3600,
            "misfire_policy": "RUN_ONCE",
            "max_concurrent_runs": 1,
        }
    }
    schedule_path = tmp_path / "schedule.yaml"
    schedule_path.write_text(yaml.safe_dump(schedule, allow_unicode=True), encoding="utf-8")
    return sources_path, schedule_path


def invoke(schedule_path: Path, sources_path: Path, out: Path, now: str) -> int:
    return scheduled_entry.main([
        "--config", str(schedule_path),
        "--mode", "live",
        "--sources", str(sources_path),
        "--out", str(out),
        "--now", now,
    ])


def test_second_wakeup_is_blocked_by_previous_manifest(tmp_path: Path, base_url: str) -> None:
    sources_path, schedule_path = write_configs(tmp_path, base_url)
    out = tmp_path / "runs"

    assert invoke(schedule_path, sources_path, out, "2026-10-18T09:00:00+08:00") == 0
    first = last_successful_run(out)
    assert first is not None

    # 5 分钟后再被唤醒：没有人工传 --last-run，但必须知道已经跑过
    assert invoke(schedule_path, sources_path, out, "2026-10-18T09:05:00+08:00") == 0
    manifests = list(out.rglob("run-manifest.json"))
    assert len(manifests) == 1, "同一计划时刻不应产生第二轮"

    trigger_files = list((out / "triggers").glob("*.json"))
    actions = sorted(
        json.loads(path.read_text(encoding="utf-8"))["action"] for path in trigger_files
    )
    assert actions == ["ALREADY_RUN", "RUN"]


def test_next_day_runs_again(tmp_path: Path, base_url: str) -> None:
    sources_path, schedule_path = write_configs(tmp_path, base_url)
    out = tmp_path / "runs"

    assert invoke(schedule_path, sources_path, out, "2026-10-18T09:00:00+08:00") == 0
    assert invoke(schedule_path, sources_path, out, "2026-10-19T09:00:00+08:00") == 0
    assert len(list(out.rglob("run-manifest.json"))) == 2


def test_run_without_out_directory_is_refused(tmp_path: Path, base_url: str) -> None:
    sources_path, schedule_path = write_configs(tmp_path, base_url)
    code = scheduled_entry.main([
        "--config", str(schedule_path),
        "--mode", "live",
        "--sources", str(sources_path),
        "--now", "2026-10-18T09:00:00+08:00",
    ])
    assert code == 2, "没有产物目录就没有上次运行的记忆，应当拒绝执行"


def test_failed_run_does_not_block_the_next_attempt(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    run_dir = out / "bad"
    run_dir.mkdir(parents=True)
    (run_dir / "run-manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-bad",
                "started_at": "2026-10-18T09:00:00+08:00",
                "run_status": "FAILED",
                "stages": [],
            }
        ),
        encoding="utf-8",
    )
    assert last_successful_run(out) is None, "失败的运行不该阻止下一次尝试"
