"""调度 + 在线链路的联调测试（WP-R7 × WP-R6）。

证明"定时触发"能驱动真实抓取链路，而不只是离线夹具。
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest
import yaml

from runtime import scheduled_entry
from runtime.live_config import load_live_config

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


def write_configs(tmp_path: Path, base_url: str, *, allow_unvalidated: bool = True) -> tuple[Path, Path]:
    sources = {
        "sources": {
            "allowed_hosts": ["127.0.0.1"],
            "timeout_seconds": 5,
            "max_attempts": 2,
            "retry_backoff_seconds": 0.01,
            "urls": [f"{base_url}/article.html", f"{base_url}/chinese.html"],
        },
        "run": {
            "demand_path": "fixtures/m1/basic/demand-snapshot.json",
            "policy_config_path": "fixtures/m1/basic/policy-config.json",
            "model_profile_id": "MODEL-PROFILE-STUB-AGENT",
            "allow_unvalidated": allow_unvalidated,
            "push": {"channel": "file", "target": str(tmp_path / "outbox")},
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


def test_scheduled_live_run_executes_and_records(tmp_path: Path, base_url: str) -> None:
    sources_path, schedule_path = write_configs(tmp_path, base_url)
    out = tmp_path / "out"

    code = scheduled_entry.main(
        [
            "--config", str(schedule_path),
            "--mode", "live",
            "--sources", str(sources_path),
            "--out", str(out),
            "--now", "2026-10-18T09:00:00+08:00",
            "--run-id", "run-sched-live-01",
        ]
    )

    assert code == 0
    trigger = json.loads((out / "trigger.json").read_text(encoding="utf-8"))
    assert trigger["action"] == "RUN"
    assert trigger["run_id"] == "run-sched-live-01"

    manifest = json.loads((out / "run-sched-live-01" / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["trigger"] == "SCHEDULE"
    assert manifest["delivery_status"] == "SUCCESS"
    assert manifest["counts"]["candidates"] == 2
    assert manifest["run_status"] == "SUCCESS"


def test_live_config_requires_urls(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "sources": {"allowed_hosts": ["127.0.0.1"]},
                "run": {
                    "demand_path": "fixtures/m1/basic/demand-snapshot.json",
                    "policy_config_path": "fixtures/m1/basic/policy-config.json",
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="urls"):
        load_live_config(path)


def test_unvalidated_stub_must_be_explicit(tmp_path: Path, base_url: str) -> None:
    sources_path, schedule_path = write_configs(tmp_path, base_url, allow_unvalidated=False)
    out = tmp_path / "out"

    code = scheduled_entry.main(
        [
            "--config", str(schedule_path),
            "--mode", "live",
            "--sources", str(sources_path),
            "--out", str(out),
            "--now", "2026-10-18T09:00:00+08:00",
        ]
    )

    assert code == 1
    manifests = list(out.rglob("run-manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["run_status"] == "FAILED"
    stages = {stage["stage"]: stage["status"] for stage in manifest["stages"]}
    assert stages["PRECHECK"] == "FAILED"
