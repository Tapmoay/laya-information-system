"""从 YAML 装配在线运行配置。

把"来源 / 抓取策略 / 需求与政策夹具 / 推送通道"集中在一个文件里，
让调度入口只关心"该不该跑"，不关心具体来源长什么样。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from delivery.push import PushConfig
from ingest.fetch import FetchPolicy
from runtime.live_run import LiveRunConfig

ROOT = Path(__file__).resolve().parents[1]


def load_live_config(path: str | Path, *, base_dir: Path | None = None) -> LiveRunConfig:
    base = Path(base_dir) if base_dir is not None else ROOT
    data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    run = dict(data.get("run") or {})
    demand_path = run.get("demand_path")
    policy_config_path = run.get("policy_config_path")
    if not demand_path or not policy_config_path:
        raise ValueError("run.demand_path 与 run.policy_config_path 必填")

    demand = json.loads((base / demand_path).read_text(encoding="utf-8"))
    policy_config = json.loads((base / policy_config_path).read_text(encoding="utf-8"))

    # run_limits 是政策编译出来的基线；来源/推送配置可以逐项覆盖它。
    limits = policy_config.get("run_limits") or {}
    fetch_defaults: dict[str, Any] = {}
    if limits.get("max_network_retries") is not None:
        fetch_defaults["max_attempts"] = limits["max_network_retries"]
    if limits.get("per_source_timeout_seconds") is not None:
        fetch_defaults["timeout_seconds"] = limits["per_source_timeout_seconds"]

    sources = dict(data.get("sources") or {})
    urls = list(sources.pop("urls", []) or [])
    if not urls:
        raise ValueError("sources.urls 不能为空")
    fetch_policy = FetchPolicy.from_mapping({"sources": {**fetch_defaults, **sources}})

    push_data = dict(run.get("push") or {})
    if limits.get("push_retry_limit") is not None:
        push_data.setdefault("max_attempts", limits["push_retry_limit"])
    push = PushConfig.from_mapping({"delivery": push_data} if push_data else {})

    return LiveRunConfig(
        urls=urls,
        demand=demand,
        policy_config=policy_config,
        fetch_policy=fetch_policy,
        push=push,
        model_profile_id=run.get("model_profile_id", "MODEL-PROFILE-STUB-AGENT"),
        judgment_language=run.get("judgment_language", "en"),
        source_language=run.get("source_language", "en"),
        discovery_method=run.get("discovery_method", "MANUAL"),
        allow_unvalidated=bool(run.get("allow_unvalidated", False)),
        policy_path=str(base / run["policy_path"]) if run.get("policy_path") else None,
        policy_doc=str(base / run["policy_doc"]) if run.get("policy_doc") else None,
        proceed_on_policy_conflict=bool(run.get("proceed_on_policy_conflict", False)),
    )
