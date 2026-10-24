"""本地 Qwen2.5 适配器（真实模型，通过 transformers 加载）。

边界与测试桩一致：只返回一个受限标签，不自报身份、不决定推荐、不处理重试与超时。
内部有两种提问策略，可用构造参数切换（评测时对比用；生产默认 fewshot）：

- fewshot：把任务写成"内容 -> 类别"的补全示例，小模型跟随示例比理解定义更稳；
- tree：把五分类拆成四个是否问题，由代码按固定顺序合成标签（判断仍是模型做的）。

两种策略都只产出标签；策略选择写在评测报告里，不写进注册表。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from capabilities.adapters.models.base import AdapterOutput

ABSTAIN_TOKEN = "EVIDENCE_INSUFFICIENT"

SYSTEM_PROMPT = (
    "你是一个严格的信息测量器。你的唯一任务是：从给定标签中选出一个最合适的标签。"
    "不要解释，不要输出标签以外的任何文字。"
)

FEWSHOT_TEMPLATE = """当前目标：{goal}

判断下面内容与当前目标的关系，只输出一个类别名。内容可能是中文或英文，按同一标准判断。

DIRECT：能直接指导本项目的构建或运行
ADJACENT：同属 AI 或设计领域，但当前项目用不上
EXPLORATORY：其他领域的深度材料，可能带来启发
UNRELATED：消费、通知、体育、娱乐内容
INSUFFICIENT_EVIDENCE：只有标题或一句话，没有实质内容

示例：
内容：开源框架新增类型化决策对象与策略引擎，附多步规划基准
类别：DIRECT

内容：一篇讲 JSON Schema 校验失败时如何降级的工程博客
类别：DIRECT

内容：一篇分析 RAG 分块策略与延迟权衡的文章
类别：ADJACENT

内容：某模型发布了新的分词器，中文 token 数降低两成
类别：ADJACENT

内容：一篇讲 1970 年代瑞士海报网格系统的文章
类别：EXPLORATORY

内容：一篇讨论爵士即兴中约束与自由的文章
类别：EXPLORATORY

内容：里斯本一家面包店的酸面包食谱
类别：UNRELATED

内容：周末联赛战报：两粒进球与一次伤病
类别：UNRELATED

内容：详见附件。
类别：INSUFFICIENT_EVIDENCE

内容：New release.
类别：INSUFFICIENT_EVIDENCE

内容：{text}
类别："""

RATIONALE_SUFFIX = """
先写一句不超过 25 字的理由，再换行写"类别："和标签。"""

TREE_QUESTIONS = [
    ("substantive", "这段内容是否有实质信息（不是只有标题、附件说明或一句空话）？"),
    ("consumer", "这段内容是否属于消费类（食谱、旅游、购物）、通知、体育或娱乐内容？"),
    ("direct", "这段内容是否直接帮助构建或运行一个个人信息决策系统（架构、契约、评测、抓取、选择、交付、工具链）？"),
    ("adjacent", "这段内容是否与 AI 技术或设计相关？"),
]

YES_NO_TEMPLATE = """{question}
只回答「是」或「否」。

内容：{text}
答案："""


def _pick_label(raw: str, options: list[str]) -> str | None:
    upper = raw.upper()
    hits: list[tuple[int, str]] = []
    for option in sorted(options, key=len, reverse=True):
        match = re.search(rf"(?<![A-Z_]){re.escape(option)}(?![A-Z_])", upper)
        if match:
            hits.append((match.start(), option))
    if not hits:
        return None
    return min(hits)[1]


@dataclass
class QwenLocalAdapter:
    model_path: str
    revision: str = "local"
    adapter_version: str = "1.0.0"
    device: str = "cuda"
    dtype: str = "bfloat16"
    max_new_tokens: int = 12
    strategy: str = "fewshot"
    _loaded: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _tokenizer: object | None = field(default=None, init=False, repr=False)
    _model: object | None = field(default=None, init=False, repr=False)

    # ---------------------------------------------------------------- 加载

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(self.model_path, local_files_only=True)
            model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                dtype=getattr(torch, self.dtype),
                device_map=self.device,
                local_files_only=True,
            )
            model.eval()
            self._tokenizer = tokenizer
            self._model = model
            self._loaded = True

    # ---------------------------------------------------------------- 推理

    def _generate(self, prompt: str, system: str | None = SYSTEM_PROMPT) -> str:
        import torch

        self._ensure_loaded()
        tokenizer = self._tokenizer
        model = self._model
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([text], return_tensors="pt").to(model.device)
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        new_tokens = output[0][inputs["input_ids"].shape[1] :]
        return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    # ---------------------------------------------------------------- 策略

    def _by_fewshot(self, text: str, goal: str, options: list[str]) -> str | None:
        raw = self._generate(FEWSHOT_TEMPLATE.format(goal=goal, text=text[:6000]))
        return _pick_label(raw, options)

    def _by_rationale(self, text: str, goal: str, options: list[str]) -> str | None:
        prompt = FEWSHOT_TEMPLATE.format(goal=goal, text=text[:6000])
        prompt = prompt.rstrip().removesuffix("类别：") + RATIONALE_SUFFIX + "\n理由："
        raw = self._generate(prompt)
        tail = raw.split("类别：")[-1] if "类别：" in raw else raw
        return _pick_label(tail, options)

    def _disambiguate_direct(self, text: str, label: str) -> str:
        """DIRECT 与 ADJACENT 的混淆占了大部分错误；用一个针对性的追问把两者分开。"""
        question = (
            "这段内容是否直接指导当前项目的构建或运行"
            "（架构、契约、评测、抓取、选择、交付、工具链）？"
        )
        raw = self._generate(
            YES_NO_TEMPLATE.format(question=question, text=text[:6000]), system=None
        )
        direct = raw.strip().startswith("是")
        if label == "DIRECT" and not direct:
            return "ADJACENT"
        if label == "ADJACENT" and direct:
            return "DIRECT"
        return label

    def _by_tree(self, text: str, options: list[str]) -> str | None:
        answers: dict[str, bool] = {}
        for key, question in TREE_QUESTIONS:
            raw = self._generate(
                YES_NO_TEMPLATE.format(question=question, text=text[:6000]),
                system=None,
            )
            answers[key] = raw.strip().startswith("是")
        if not answers["substantive"]:
            label = "INSUFFICIENT_EVIDENCE"
        elif answers["consumer"]:
            label = "UNRELATED"
        elif answers["direct"]:
            label = "DIRECT"
        elif answers["adjacent"]:
            label = "ADJACENT"
        else:
            label = "EXPLORATORY"
        return label if label in options else None

    # ---------------------------------------------------------------- 接口

    def evaluate(self, request: dict) -> AdapterOutput:
        options = list(request["question"].get("options") or [])
        text = (request["state"].get("candidate_text") or "").strip()
        goal = request["question"].get("instruction") or "构建个人信息决策系统"

        if self.strategy == "tree":
            label = self._by_tree(text, options)
        elif self.strategy == "rationale":
            label = self._by_rationale(text, goal, options)
        elif self.strategy == "disambig":
            label = self._by_fewshot(text, goal, options)
            if label in {"DIRECT", "ADJACENT"}:
                label = self._disambiguate_direct(text, label)
        else:
            label = self._by_fewshot(text, goal, options)

        if label is None:
            return AdapterOutput(
                status="ABSTAIN",
                reason_code="EVIDENCE_INSUFFICIENT",
                evidence_refs=list(request["state"].get("evidence_refs") or []),
            )
        return AdapterOutput(
            status="OK",
            label=label,
            evidence_refs=list(request["state"].get("evidence_refs") or []),
            probabilities=None,
        )
