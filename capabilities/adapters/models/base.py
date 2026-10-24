"""测量适配器的最小接口（CAP-MEA-01 的边界）。

适配器只负责"给出一个受限的测量结论"，不负责：
- 决定推荐什么（那是 Selector 的事）；
- 自己填写 model_profile_id / revision（由调用方从注册表填入，防止自报身份）；
- 处理语言路由、超时、重试（由 invoke 统一处理）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class AdapterError(RuntimeError):
    """适配器不可用、未授权或输出不合法。"""


@dataclass
class AdapterOutput:
    status: str = "OK"
    label: str | None = None
    reason_code: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    probabilities: dict[str, float] | None = None


class DecisionCapability(Protocol):
    adapter_version: str

    def evaluate(self, request: dict) -> AdapterOutput: ...
