"""运行清单装配（CAP-RUN-03 的雏形）。

run 状态与推送状态分开：M1 不推送，但清单里明确写 NOT_ATTEMPTED，
而不是把"没推"混进"成功"里。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def derive_run_status(stages: list[dict[str, Any]]) -> str:
    statuses = [stage["status"] for stage in stages]
    if "FAILED" in statuses:
        return "FAILED"
    if "PARTIAL" in statuses:
        return "PARTIAL"
    return "SUCCESS"


def build_manifest(
    run_id: str,
    trigger: str,
    started_at: datetime,
    finished_at: datetime,
    timezone: str,
    policy_version: str,
    contract_versions: dict[str, str],
    model_revisions: list[dict[str, str]],
    stages: list[dict[str, Any]],
    delivery_status: str,
    counts: dict[str, int],
    artifacts: list[str],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "trigger": trigger,
        "planned_at": None,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "timezone": timezone,
        "policy_version": policy_version,
        "contract_versions": contract_versions,
        "model_revisions": model_revisions,
        "stages": stages,
        "run_status": derive_run_status(stages),
        "delivery_status": delivery_status,
        "counts": counts,
        "artifacts": artifacts,
        "contract_version": "1.0",
    }
