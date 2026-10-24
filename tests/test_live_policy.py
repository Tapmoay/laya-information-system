"""在线链路的政策输入：与离线一致，冲突默认阻断。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ingest.fetch import FetchPolicy
from runtime.live_run import LiveRunConfig, run_live

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "information-policy.md"
CASE = ROOT / "fixtures" / "m1" / "basic"
NOW = datetime.fromisoformat("2026-10-18T05:55:00+08:00")

CONFLICTING = """# 测试用政策

- 当前版本：v0.3

## 3.8 容量

- 常规核心信息：最多 5 条。
- 常规跨领域探索：最多 2 条。
- 视觉通道单独运行时：最多 5 项匹配推荐 + 2 项探索推荐。
- S 级重大技术变化可单独提示。

## 10. 政策修改与变更日志

- 当前版本：v0.2。
"""


def write_conflicting_policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy-conflict.md"
    path.write_text(CONFLICTING, encoding="utf-8")
    return path


def live(policy_doc: str | None, *, proceed: bool = False) -> LiveRunConfig:
    return LiveRunConfig(
        urls=["http://127.0.0.1:9/never.html"],
        demand=json.loads((CASE / "demand-snapshot.json").read_text(encoding="utf-8")),
        policy_config=json.loads((CASE / "policy-config.json").read_text(encoding="utf-8")),
        fetch_policy=FetchPolicy(allowed_hosts=("127.0.0.1",), max_attempts=1, timeout_seconds=1),
        policy_doc=policy_doc,
        proceed_on_policy_conflict=proceed,
    )


def test_live_run_blocks_on_policy_conflict(tmp_path: Path) -> None:
    conflict = write_conflicting_policy(tmp_path)
    result = run_live(live(str(conflict)), run_id="live-policy-01", out_dir=tmp_path / "run", now=NOW)
    assert result["failed_stage"] == "PRECHECK"
    manifest = json.loads((tmp_path / "run" / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_status"] == "FAILED"


def test_live_run_records_compiled_policy_when_allowed(tmp_path: Path) -> None:
    result = run_live(live(str(POLICY)), run_id="live-policy-02", out_dir=tmp_path / "run", now=NOW)
    # 预检通过后会去抓 9 端口并失败，但政策产物应当已经落下
    assert result["failed_stage"] != "PRECHECK"
    bundle = json.loads((tmp_path / "run" / "policy-bundle.json").read_text(encoding="utf-8"))
    assert bundle["policy_version"] == "v0.3"
    config = json.loads((tmp_path / "run" / "policy-config.json").read_text(encoding="utf-8"))
    assert config["capacity"]["core_items_max"] == 5
