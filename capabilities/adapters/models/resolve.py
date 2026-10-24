"""按模型档案构造适配器。

STUB 与 REAL 走同一条边界：调用方只拿到一个 DecisionCapability，
不知道（也不需要知道）背后是关键词匹配还是 1.5B 参数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_PATH = ROOT / "models" / "runtime.yaml"


def load_runtime(path: Path | None = None) -> dict[str, dict[str, Any]]:
    candidate = path or RUNTIME_PATH
    if not candidate.exists():
        return {}
    data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    return dict(data.get("profiles") or {})


def resolve_adapter(
    profile: dict[str, Any],
    runtime: dict[str, dict[str, Any]] | None = None,
    *,
    strategy: str | None = None,
):
    kind = profile["implementation_kind"]
    profile_id = profile["model_profile_id"]

    if kind == "STUB":
        from capabilities.adapters.models.stub import agent_stub, visual_stub

        stubs = {
            "MODEL-PROFILE-STUB-AGENT": agent_stub,
            "MODEL-PROFILE-STUB-VISUAL": visual_stub,
        }
        if profile_id not in stubs:
            raise KeyError(f"{profile_id} 没有对应的测试桩")
        return stubs[profile_id]()

    if kind == "REAL":
        entry = (runtime if runtime is not None else load_runtime()).get(profile_id)
        if not entry:
            raise KeyError(f"{profile_id} 缺少本机运行时映射（models/runtime.yaml）")
        if "strands" in str(profile.get("provider_or_runtime", "")):
            from capabilities.adapters.models.decider import StrandsDeciderAdapter

            return StrandsDeciderAdapter(
                model_path=str(ROOT / entry["model_path"]),
                revision=profile["revision"],
                adapter_version=profile["adapter_version"],
                device=entry.get("device", "cuda"),
            )

        from capabilities.adapters.models.qwen_local import QwenLocalAdapter

        kwargs: dict[str, Any] = {
            "model_path": str(ROOT / entry["model_path"]),
            "revision": profile["revision"],
            "adapter_version": profile["adapter_version"],
            "device": entry.get("device", "cuda"),
            "dtype": entry.get("dtype", "bfloat16"),
        }
        if strategy:
            kwargs["strategy"] = strategy
        return QwenLocalAdapter(**kwargs)

    raise KeyError(f"未知的 implementation_kind：{kind}")
