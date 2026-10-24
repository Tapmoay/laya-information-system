"""运行阶段与状态推导（v0.3 §2.1）。

阶段名是"运行阶段"，不是能力阶段；两者的映射见 docs/01-open-conflicts.md C-12。
"""

from __future__ import annotations

from typing import Iterable

RUN_STAGES = (
    "PRECHECK",
    "DEMAND_BUILD",
    "DISCOVERY",
    "NORMALIZATION",
    "MEASUREMENT",
    "SELECTION",
    "DELIVERY_BUILD",
    "PUSH",
    "PERSIST_RUN_REPORT",
    "RELEASE_RESOURCES",
)

STAGE_STATUSES = ("OK", "PARTIAL", "FAILED", "SKIPPED", "BLOCKED")
RUN_STATUSES = ("SUCCESS", "PARTIAL", "FAILED", "ABORTED")


def derive_run_status(statuses: Iterable[str], aborted: bool = False) -> str:
    """run 状态由阶段推导，不允许调用方自己填一个"看起来对"的值。

    BLOCKED 表示有阶段因为超时或上游失败而没执行，不能算成功。
    """
    values = list(statuses)
    if "FAILED" in values:
        return "FAILED"
    if aborted:
        return "ABORTED"
    if "PARTIAL" in values or "BLOCKED" in values:
        return "PARTIAL"
    return "SUCCESS"

