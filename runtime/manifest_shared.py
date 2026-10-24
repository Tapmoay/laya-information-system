"""运行清单装配（共享）。

把"阶段结果 + 计数 + 产物"变成合法的 RunManifest，并保证 run 状态由阶段推导，
而不是调用方自己填。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _stamp(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def finalize_manifest(
    *,
    run_id: str,
    trigger: str,
    at: datetime,
    timezone: str,
    policy_version: str,
    contract_versions: dict[str, str],
    model_revisions: list[dict[str, str]],
    stages: list[dict[str, Any]],
    delivery_status: str,
    counts: dict[str, int],
    artifacts: list[str],
) -> dict[str, Any]:
    entries = list(stages)
    entries.append(
        {"stage": "PERSIST_RUN_REPORT", "status": "OK", "started_at": _stamp(at), "finished_at": _stamp(at), "error_ref": None}
    )
    entries.append(
        {"stage": "RELEASE_RESOURCES", "status": "SKIPPED", "started_at": None, "finished_at": None, "error_ref": None}
    )

    statuses = [entry["status"] for entry in entries]
    if "FAILED" in statuses:
        run_status = "FAILED"
    elif "PARTIAL" in statuses or "BLOCKED" in statuses:
        run_status = "PARTIAL"
    else:
        run_status = "SUCCESS"

    return {
        "run_id": run_id,
        "trigger": trigger,
        "planned_at": None,
        "started_at": _stamp(at),
        "finished_at": _stamp(at),
        "timezone": timezone,
        "policy_version": policy_version,
        "contract_versions": contract_versions,
        "model_revisions": model_revisions,
        "stages": entries,
        "run_status": run_status,
        "delivery_status": delivery_status,
        "counts": counts,
        "artifacts": artifacts,
        "contract_version": "1.0",
    }
