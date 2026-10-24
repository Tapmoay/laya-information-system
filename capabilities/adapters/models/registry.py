"""模型注册表加载与校验（v0.3 §5.5）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "models" / "registry.yaml"


def load_profiles(path: Path | None = None) -> list[dict[str, Any]]:
    data = yaml.safe_load((path or REGISTRY_PATH).read_text(encoding="utf-8")) or {}
    return list(data.get("profiles") or [])


def validate_profiles(profiles: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        profile_id = profile.get("model_profile_id", "<unknown>")
        if profile_id in seen:
            errors.append(f"{profile_id}: 重复注册")
        seen.add(profile_id)
        for problem in validate("model-profile", profile):
            errors.append(f"{profile_id}: {problem}")
    return errors


def get_profile(profiles: list[dict[str, Any]], model_profile_id: str) -> dict[str, Any]:
    for profile in profiles:
        if profile["model_profile_id"] == model_profile_id:
            return profile
    raise KeyError(f"未注册的模型：{model_profile_id}")
