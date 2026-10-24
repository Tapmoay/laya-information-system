"""政策读取与编译回归测试（CAP-POL-01/02）。

系统的根基是"读政策"，这里第一次让它真的去读。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from policy.compiler import PolicyCompileError, compile_policy_config
from policy.loader import PolicyLoadError, load_policy_bundle
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "information-policy.md"
NOW = datetime.fromisoformat("2026-10-18T05:30:00+08:00")


def bundle(**kwargs):
    return load_policy_bundle(POLICY, compiled_at=NOW, **kwargs)


# ---------------------------------------------------------------- 读取


FAKE_POLICY = """# 测试用假政策

- 当前版本：\x60{v_top}\x60

## 3.8 容量

- 常规核心信息：最多 5 条。
- 常规跨领域探索：最多 2 条。
- 视觉通道单独运行时：最多 5 项匹配推荐 + 2 项探索推荐。
- S 级重大技术变化可单独提示，但必须解释原因。

## 10. 政策修改与变更日志

- 当前版本：\x60{v_log}\x60。
"""


def write_policy(tmp_path: Path, top: str = "v0.3", changelog: str = "v0.2") -> Path:
    path = tmp_path / "policy.md"
    path.write_text(FAKE_POLICY.format(v_top=top, v_log=changelog), encoding="utf-8")
    return path


def test_real_policy_is_self_consistent() -> None:
    """C-01 已由用户裁决：§10 与顶部同为 v0.3，冲突消失，运行不再默认被拦。"""
    payload = bundle()
    policy_file = payload["files"][0]
    assert policy_file["declared_version"] == "v0.3"
    assert policy_file["declared_version_in_changelog"] == "v0.3"
    assert payload["version_conflicts"] == []
    assert payload["state"]["status"] == "OK"
    assert validate("policy-bundle", payload) == []


def test_self_declared_conflict_blocks_by_default(tmp_path: Path) -> None:
    payload = load_policy_bundle(write_policy(tmp_path, "v0.3", "v0.2"), compiled_at=NOW)
    assert payload["state"]["status"] == "INVALID"
    assert payload["version_conflicts"][0]["kind"] == "SELF_DECLARED_MISMATCH"


def test_conflict_can_be_acknowledged(tmp_path: Path) -> None:
    payload = load_policy_bundle(
        write_policy(tmp_path, "v0.3", "v0.2"), proceed_allowed=True, compiled_at=NOW
    )
    assert payload["state"]["status"] == "PARTIAL"
    assert payload["state"]["reason_code"] == "VERSION_CONFLICT"


def test_expected_version_mismatch_is_recorded() -> None:
    payload = bundle(expected_version="v0.4", proceed_allowed=True)
    kinds = {item["kind"] for item in payload["version_conflicts"]}
    assert kinds == {"EXPECTED_VERSION_MISMATCH"}


def test_hash_is_stable_and_missing_file_raises(tmp_path: Path) -> None:
    first = bundle()
    second = bundle()
    assert first["files"][0]["sha256"] == second["files"][0]["sha256"]
    with pytest.raises(PolicyLoadError):
        load_policy_bundle(tmp_path / "nope.md")


def test_extra_goal_file_is_included() -> None:
    payload = load_policy_bundle(
        POLICY,
        extra_files=[("GOAL", ROOT / "LaYA-technical-roadmap-v0.3.md")],
        proceed_allowed=True,
        compiled_at=NOW,
    )
    roles = [item["role"] for item in payload["files"]]
    assert roles == ["POLICY", "GOAL"]


# ---------------------------------------------------------------- 编译


def test_capacity_is_extracted_from_policy_text() -> None:
    config = compile_policy_config(bundle(proceed_allowed=True), compiled_at=NOW)
    assert config["capacity"] == {
        "core_items_max": 5,
        "exploration_items_max": 2,
        "visual_match_max": 5,
        "visual_exploration_max": 2,
        "s_breakout_allowed": True,
    }
    assert len(config["capacity_sources"]) == 5
    assert validate("policy-config", config) == []


def test_language_defaults_follow_adr_0008() -> None:
    config = compile_policy_config(bundle(proceed_allowed=True), compiled_at=NOW)
    assert config["language"]["delivery_language_mode"] == "ZH_TRANSLATION"
    assert config["language"]["show_original_title"] is True
    assert config["language"]["translated_text_is_evidence"] is False


def test_language_can_be_overridden() -> None:
    config = compile_policy_config(
        bundle(proceed_allowed=True),
        language={"delivery_language_mode": "BILINGUAL"},
        compiled_at=NOW,
    )
    assert config["language"]["delivery_language_mode"] == "BILINGUAL"


def test_compile_fails_loudly_when_clause_is_missing(tmp_path: Path) -> None:
    fake = tmp_path / "fake-policy.md"
    fake.write_text("# Fake\n\n- 当前版本：\x60v9.9\x60\n\n## 3.8 容量\n\n- 常规核心信息：最多 5 条。\n", encoding="utf-8")
    payload = load_policy_bundle(fake, compiled_at=NOW)
    with pytest.raises(PolicyCompileError, match="常规跨领域探索"):
        compile_policy_config(payload, compiled_at=NOW)


def test_config_hash_is_deterministic() -> None:
    first = compile_policy_config(bundle(proceed_allowed=True), compiled_at=NOW)
    second = compile_policy_config(bundle(proceed_allowed=True), compiled_at=NOW)
    assert first["config_hash"] == second["config_hash"]


def test_config_hash_ignores_nothing_it_should_not() -> None:
    base = compile_policy_config(bundle(proceed_allowed=True), compiled_at=NOW)
    changed = compile_policy_config(
        bundle(proceed_allowed=True), language={"delivery_language_mode": "ORIGINAL"}, compiled_at=NOW
    )
    assert base["config_hash"] != changed["config_hash"]
