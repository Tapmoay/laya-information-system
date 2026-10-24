"""Strands Decider 2B 适配器：typed decision（choice/score/noul），输出概率。

与通用模型的关键差别：它不做自由文本生成，而是对每个选项给出 logit，
softmax 之后就是概率分布——路由层拿到的就是"模型有多确定"。

依赖装在独立目录 models/_tf5（transformers 5.x），不污染主环境的 4.57。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from capabilities.adapters.models.base import AdapterOutput

ROOT = Path(__file__).resolve().parents[3]
TF5_DIR = ROOT / "models" / "_tf5"

CRITERIA = {
    "DIRECT": "直接帮助构建或运行当前项目：架构、契约、评测、抓取、选择、交付、工具链",
    "ADJACENT": "同属 AI 或设计领域，但当前项目用不上",
    "EXPLORATORY": "其他领域的深度材料，可能带来启发",
    "UNRELATED": "消费、通知、购物、体育、娱乐内容",
    "INSUFFICIENT_EVIDENCE": "内容太短或没有实质信息",
}


@dataclass
class StrandsDeciderAdapter:
    model_path: str
    revision: str = "local"
    adapter_version: str = "1.0.0"
    device: str = "cuda"
    _loaded: bool = field(default=False, init=False, repr=False)
    _engine: Any = field(default=None, init=False, repr=False)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # transformers 5.x 只对这个进程可见；主环境的 4.57 不受影响
        if str(TF5_DIR) not in sys.path:
            sys.path.insert(0, str(TF5_DIR))
        from strands_decider.infer import load_engine

        self._engine = load_engine(self.model_path, device=self.device)
        self._loaded = True

    def evaluate(self, request: dict) -> AdapterOutput:
        self._ensure_loaded()
        from strands_decider import schema

        options = list(request["question"].get("options") or [])
        criteria = {option: CRITERIA.get(option, option) for option in options}
        question = schema.ChoiceQuestion(
            instructions=request["question"].get("instruction") or "判断内容与目标的关系类别",
            criteria=criteria,
        )
        text = (request["state"].get("candidate_text") or "")[:4000]
        response = self._engine.ask(text, {"relevance": question})
        answer = response.answers["relevance"]
        return AdapterOutput(
            status="OK",
            label=answer.choice,
            evidence_refs=list(request["state"].get("evidence_refs") or []),
            probabilities=dict(answer.probabilities),
        )


def build(model_path: str, revision: str, adapter_version: str, device: str = "cuda"):
    return StrandsDeciderAdapter(
        model_path=model_path,
        revision=revision,
        adapter_version=adapter_version,
        device=device,
    )
