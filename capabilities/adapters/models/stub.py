"""确定性测试桩。

用途只有一个：在没有模型的情况下验证适配器边界、结果映射和"换模型不改业务流程"。
它不模拟模型质量，也不得用于正式运行（registry 里 implementation_kind=STUB）。
"""

from __future__ import annotations

from dataclasses import dataclass

from capabilities.adapters.models.base import AdapterOutput


@dataclass
class KeywordStubAdapter:
    keywords: tuple[str, ...]
    hit_label: str
    default_label: str
    adapter_version: str = "0.1.0"

    def evaluate(self, request: dict) -> AdapterOutput:
        text = request["state"]["candidate_text"].casefold()
        hit = any(keyword.casefold() in text for keyword in self.keywords)
        return AdapterOutput(
            status="OK",
            label=self.hit_label if hit else self.default_label,
            evidence_refs=list(request["state"].get("evidence_refs") or []),
            probabilities=None,
        )


def agent_stub() -> KeywordStubAdapter:
    return KeywordStubAdapter(
        keywords=("agent", "model", "decision", "模型", "决策", "评测"),
        hit_label="ADJACENT",
        default_label="UNRELATED",
    )


def visual_stub() -> KeywordStubAdapter:
    return KeywordStubAdapter(
        keywords=("visual", "narrative", "poster", "视觉", "叙事", "海报", "构图"),
        hit_label="ADJACENT",
        default_label="UNRELATED",
    )
