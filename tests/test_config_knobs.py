"""PolicyConfig 的每个字段要么被真的执行，要么被明确标记为"尚未接通"。

存在的理由：上一轮发现 s_breakout_allowed 被解析、被存储、被传进选择器，
然后被忽略——一条政策规则静默失效。这个测试让同类缺口无法再悄悄出现：
新增字段必须归类，未接通的必须写清原因。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from runtime.live_config import load_live_config
from runtime.offline_run import run_case
from runtime.run_manager import RunManager

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"
CASE = ROOT / "fixtures" / "m1" / "basic"
NOW = datetime.fromisoformat("2026-10-18T06:20:00+08:00")

# 元数据字段：记录身份与来源，不是可调的执行参数
META = {
    "policy_version",
    "policy_bundle_ref",
    "capacity_sources",
    "config_id",
    "compiled_at",
    "config_hash",
    "contract_version",
}

# 有自动化测试证明"改它会改变行为"
ENFORCED = {
    "capacity.core_items_max",
    "capacity.exploration_items_max",
    "capacity.s_breakout_allowed",
    "language.delivery_language_mode",
    "run_limits.max_duration_minutes",
    "run_limits.max_network_retries",
    "run_limits.per_source_timeout_seconds",
    "run_limits.push_retry_limit",
}

# 声明了但还没接通：必须写清缺什么
DECLARED_NOT_ENFORCED = {
    "capacity.visual_match_max": "视觉通道尚未实现，选择器目前只有 CORE / EXPLORATION 两个通道",
    "capacity.visual_exploration_max": "视觉通道尚未实现，探索配额同样无处可用",
    "language.retrieval_mode": "没有检索器（CAP-INF-01/02 仍是 CONTRACTED）",
    "language.allowed_retrieval_languages": "检索器尚未实现，允许的检索语种清单没有消费者",
    "language.judgment_language_policy": "目前所有能力都走单一路径，尚未按能力选择判断语言",
    "language.show_original_title": "渲染层恒显示原文标题，开关没有接",
    "language.translated_text_is_evidence": "还没有翻译步骤，所以恒为 false",
    "language.verify_critical_claims_against_source": "没有二次核对步骤",
    "run_limits.model_timeout_seconds": "没有真实模型可超时",
    "run_limits.stop_on_policy_version_mismatch": "冲突处理走 proceed_on_policy_conflict 参数",
}


def config_keys() -> list[str]:
    schema = json.loads((SCHEMAS / "policy-config-1.0.schema.json").read_text(encoding="utf-8"))
    keys: list[str] = []

    def walk(props: dict, prefix: str = "") -> None:
        for name, spec in props.items():
            path = prefix + name
            if spec.get("type") == "object" and "properties" in spec:
                walk(spec["properties"], path + ".")
            else:
                keys.append(path)

    walk(schema["properties"])
    return keys


def test_every_config_key_is_classified() -> None:
    keys = set(config_keys())
    declared = set(DECLARED_NOT_ENFORCED)
    assert not (ENFORCED & declared), "同一个字段不能既算已接通又算未接通"
    assert keys - META == ENFORCED | declared
    assert ENFORCED <= keys and declared <= keys


def test_declared_keys_say_what_is_missing() -> None:
    for key, reason in DECLARED_NOT_ENFORCED.items():
        assert len(reason) >= 8, f"{key} 的说明太短"


def write_live_config(tmp_path: Path, *, sources: dict, push: dict | None = None) -> Path:
    manifest = {
        "sources": {"allowed_hosts": ["127.0.0.1"], "urls": ["http://127.0.0.1:1/a.html"], **sources},
        "run": {
            "demand_path": str(CASE / "demand-snapshot.json"),
            "policy_config_path": str(tmp_path / "policy-config.json"),
        },
    }
    if push is not None:
        manifest["run"]["push"] = push
    path = tmp_path / "sources.yaml"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_policy_config(tmp_path: Path, limits: dict) -> None:
    config = json.loads((CASE / "policy-config.json").read_text(encoding="utf-8"))
    config["run_limits"].update(limits)
    (tmp_path / "policy-config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def test_fetch_limits_come_from_policy_config(tmp_path: Path) -> None:
    write_policy_config(tmp_path, {"max_network_retries": 5, "per_source_timeout_seconds": 12})
    path = write_live_config(tmp_path, sources={})
    live = load_live_config(path, base_dir=ROOT)
    assert live.fetch_policy.max_attempts == 5
    assert live.fetch_policy.timeout_seconds == 12


def test_source_config_can_override_fetch_limits(tmp_path: Path) -> None:
    write_policy_config(tmp_path, {"max_network_retries": 5, "per_source_timeout_seconds": 12})
    path = write_live_config(tmp_path, sources={"max_attempts": 2, "timeout_seconds": 3})
    live = load_live_config(path, base_dir=ROOT)
    assert live.fetch_policy.max_attempts == 2
    assert live.fetch_policy.timeout_seconds == 3


def test_push_retry_limit_reaches_push_config(tmp_path: Path) -> None:
    write_policy_config(tmp_path, {"push_retry_limit": 7})
    path = write_live_config(tmp_path, sources={}, push={"channel": "file", "target": "outbox"})
    live = load_live_config(path, base_dir=ROOT)
    assert live.push.max_attempts == 7
    assert live.push.channel == "file"


def test_explicit_push_attempts_win(tmp_path: Path) -> None:
    write_policy_config(tmp_path, {"push_retry_limit": 7})
    path = write_live_config(
        tmp_path, sources={}, push={"channel": "file", "target": "outbox", "max_attempts": 2}
    )
    live = load_live_config(path, base_dir=ROOT)
    assert live.push.max_attempts == 2


def test_time_budget_comes_from_policy_config(tmp_path: Path, monkeypatch) -> None:
    case = tmp_path / "case"
    shutil.copytree(CASE, case)
    config = json.loads((case / "policy-config.json").read_text(encoding="utf-8"))
    config["run_limits"]["max_duration_minutes"] = 7
    (case / "policy-config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    captured: dict = {}
    original = RunManager.__init__

    def spy(self, *args, **kwargs):
        captured.update(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RunManager, "__init__", spy)
    run_case(case, out_dir=tmp_path / "run", run_id="budget-01", now=NOW)

    assert captured["max_duration_seconds"] == pytest.approx(420.0)


def test_no_budget_means_no_deadline(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}
    original = RunManager.__init__

    def spy(self, *args, **kwargs):
        captured.update(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(RunManager, "__init__", spy)
    run_case(CASE, out_dir=tmp_path / "run", run_id="budget-02", now=NOW)
    assert captured["max_duration_seconds"] is None
