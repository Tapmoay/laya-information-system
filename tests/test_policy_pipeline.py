"""政策到运行的集成测试。

证明编译出来的配置真的能驱动一轮运行，而不是躺在 policy 目录里当摆设。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from policy.compiler import compile_policy_config
from policy.loader import load_policy_bundle
from runtime.offline_run import run_case
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "information-policy.md"
CASE = ROOT / "fixtures" / "m1" / "basic"
NOW = datetime.fromisoformat("2026-10-18T05:40:00+08:00")

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


def test_run_uses_config_compiled_from_policy(tmp_path: Path) -> None:
    outcome = run_case(
        CASE,
        out_dir=tmp_path / "run",
        run_id="run-policy-01",
        now=NOW,
        policy_doc=POLICY,
    )

    manifest = outcome["manifest"]
    assert manifest["run_status"] == "SUCCESS", outcome["error"]
    assert manifest["policy_version"] == "v0.3"

    compiled = json.loads((tmp_path / "run" / "policy-config.json").read_text(encoding="utf-8"))
    assert compiled["capacity"]["core_items_max"] == 5
    assert compiled["capacity"]["exploration_items_max"] == 2
    assert validate("policy-config", compiled) == []

    bundle = json.loads((tmp_path / "run" / "policy-bundle.json").read_text(encoding="utf-8"))
    assert bundle["policy_version"] == "v0.3"
    assert bundle["state"]["status"] == "OK", "C-01 已裁决，政策自洽"


def test_policy_conflict_blocks_the_run_by_default(tmp_path: Path) -> None:
    outcome = run_case(
        CASE,
        out_dir=tmp_path / "run",
        run_id="run-policy-02",
        now=NOW,
        policy_doc=write_conflicting_policy(tmp_path),
    )
    assert outcome["failed_stage"] == "PRECHECK"
    assert "VERSION_CONFLICT" in (outcome["error"] or "")
    manifest = json.loads((tmp_path / "run" / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_status"] == "FAILED"


def test_compiled_config_matches_fixture_capacity() -> None:
    fixture = json.loads((CASE / "policy-config.json").read_text(encoding="utf-8"))
    bundle = load_policy_bundle(POLICY, compiled_at=NOW)
    compiled = compile_policy_config(bundle, compiled_at=NOW)
    assert compiled["capacity"] == fixture["capacity"], "夹具容量应当与政策一致，否则夹具已过期"
