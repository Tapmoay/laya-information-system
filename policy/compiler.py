"""把政策文本编译成机器可读的 PolicyConfig（CAP-POL-02）。

原则：**找不到就报错，不给默认值。** 容量上限必须从政策条款里抽出来；
抽不到说明政策措辞变了，应该有人去看，而不是让代码悄悄用上一版数字。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.validate_objects import validate

CORE_RE = re.compile(r"常规核心信息[：:]\s*最多\s*(\d+)\s*条")
EXPLORE_RE = re.compile(r"常规跨领域探索[：:]\s*最多\s*(\d+)\s*条")
VISUAL_RE = re.compile(r"视觉通道单独运行时[：:]\s*最多\s*(\d+)\s*项匹配推荐\s*\+\s*(\d+)\s*项探索推荐")
S_BREAK_RE = re.compile(r"S\s*级重大技术变化可单独提示")

DEFAULT_LANGUAGE: dict[str, Any] = {
    "retrieval_mode": "DYNAMIC_MULTILINGUAL",
    "allowed_retrieval_languages": ["en", "zh-Hans"],
    "judgment_language_policy": "BEST_VALIDATED_PATH_PER_CAPABILITY",
    "delivery_language_mode": "ZH_TRANSLATION",
    "show_original_title": True,
    "translated_text_is_evidence": False,
    "verify_critical_claims_against_source": True,
}

DEFAULT_RUN_LIMITS: dict[str, Any] = {
    "max_duration_minutes": None,
    "max_network_retries": 2,
    "per_source_timeout_seconds": None,
    "model_timeout_seconds": None,
    "push_retry_limit": 3,
    "stop_on_policy_version_mismatch": True,
}


class PolicyCompileError(ValueError):
    pass


def _extract(pattern: re.Pattern[str], text: str, label: str) -> re.Match[str]:
    match = pattern.search(text)
    if match is None:
        raise PolicyCompileError(f"政策文本里找不到「{label}」条款，拒绝用默认值代替")
    return match


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compile_policy_config(
    bundle: dict[str, Any],
    *,
    policy_path: Path | str | None = None,
    language: dict[str, Any] | None = None,
    run_limits: dict[str, Any] | None = None,
    config_id: str = "config-local-0001",
    compiled_at: datetime | None = None,
) -> dict[str, Any]:
    compiled_at = compiled_at or datetime.now().astimezone()
    path = Path(policy_path or bundle["files"][0]["path"])
    text = path.read_text(encoding="utf-8")
    clause = f"{path.name} §3.8"

    core = int(_extract(CORE_RE, text, "常规核心信息：最多 N 条").group(1))
    explore = int(_extract(EXPLORE_RE, text, "常规跨领域探索：最多 N 条").group(1))
    visual = _extract(VISUAL_RE, text, "视觉通道单独运行时：最多 N 项匹配推荐 + M 项探索推荐")
    s_breakout = S_BREAK_RE.search(text) is not None
    if not s_breakout:
        raise PolicyCompileError("政策文本里找不到「S 级重大技术变化可单独提示」，拒绝猜测是否允许突破容量")

    capacity = {
        "core_items_max": core,
        "exploration_items_max": explore,
        "visual_match_max": int(visual.group(1)),
        "visual_exploration_max": int(visual.group(2)),
        "s_breakout_allowed": s_breakout,
    }
    capacity_sources = [{"field": field, "clause": clause} for field in capacity]

    merged_language = {**DEFAULT_LANGUAGE, **(language or {})}
    merged_limits = {**DEFAULT_RUN_LIMITS, **(run_limits or {})}

    config = {
        "config_id": config_id,
        "compiled_at": compiled_at.isoformat(timespec="seconds"),
        "policy_version": bundle["policy_version"],
        "policy_bundle_ref": bundle["bundle_id"],
        "capacity": capacity,
        "capacity_sources": capacity_sources,
        "language": merged_language,
        "run_limits": merged_limits,
        "contract_version": "1.0",
    }
    config["config_hash"] = _canonical_hash(config)

    problems = validate("policy-config", config)
    if problems:
        raise PolicyCompileError("机器配置违反契约：" + "; ".join(problems))
    return config
