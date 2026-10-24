"""固定结构的问句模板，只替换其中的目标对象。

结构永远一样（4 个方向槽 + 4 项价值 + 2 项质量 + 2 项形态），
换的是槽里的文字：系统架构 / 视觉趋势 / 建筑 / 生物 / 音乐……
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 结构固定：四个方向槽，缺省时用占位符，不改变问题数量
SLOT_TEMPLATES = {
    "primary": "这条内容是否与「{text}」直接相关，并可能推进它？",
    "secondary": "这条内容是否与「{text}」相关？",
    "exploration": "这条内容是否提供了值得检验的陌生视角或跨领域联系（不限于已列方向）？{text}",
    "task": "这条内容是否能帮助完成当前任务「{text}」？",
}

FALLBACK = {
    "primary": "用户尚未声明的主要方向",
    "secondary": "用户尚未声明的次要方向",
    "exploration": "用户尚未声明的探索方向",
    "task": "当前没有明确任务",
}

# 政策 §2 的五项价值：固定不变，不随用户领域改变
POLICY_VALUES = {
    "value_task": "这条内容是否能帮助解决当前问题、推进项目或解除关键阻塞？",
    "value_capability": "这条内容是否能帮助形成可重复使用的知识、技能或认知模型？",
    "value_decision": "这条内容是否可能改变重要判断、选择、路线或行动？",
    "value_exploration": "这条内容是否提供值得检验的新视角、陌生领域或跨领域联系？",
    "value_environment": "这条内容是否揭示可能影响未来决策的重要技术、行业或外部变化？",
}

QUALITY = {
    "quality_testable": "这条内容是否能对应到证据、实验、原始材料或可观察的结果？",
    "quality_increment": "这条内容是否相对已有知识增加了新的事实、解释、联系或判断依据？",
}

FORM_CRITERIA = {
    "TECHNICAL": "技术实质：讲机制、方法、实验、数据或可复现的实现",
    "PRODUCT": "产品公告：发布或更新某个产品、平台、服务，说明功能与能力",
    "EVENT": "活动预告：会议、论坛、课程、直播的通知或议程介绍",
    "RECRUIT": "招聘招募：招人、征集建议、悬赏、合作邀约",
    "MARKETING": "营销宣传：公司、城市或行业推广，观点软文，情绪化吹捧",
}

RECRUIT_QUESTION = "这条内容的主体目的是不是在招人、征集建议、悬赏或寻求合作？"


@dataclass(frozen=True)
class QuestionSpec:
    name: str
    kind: str
    instructions: str
    criteria: dict[str, str] = field(default_factory=dict)
    tag: str = ""
    available: bool = True


def slot_values(demand: dict[str, Any]) -> dict[str, str]:
    """把需求映射到四个固定槽位。多出来的方向不额外加题。"""
    goals = [item.get("statement", "").strip() for item in (demand.get("active_goals") or [])]
    tasks = [item.get("statement", "").strip() for item in (demand.get("active_tasks") or [])]
    hint = demand.get("exploration_intent") or {}
    domains = [item for item in (hint.get("domains_hint") or []) if item.strip()]
    exploration_hint = domains[0] if domains else (hint.get("statement") or "")
    values = {
        "primary": goals[0] if len(goals) > 0 else "",
        "secondary": goals[1] if len(goals) > 1 else "",
        # 探索槽优先用"抽象概念在其他领域的体现"，其次才回退到第三个目标
        "exploration": exploration_hint or (goals[2] if len(goals) > 2 else ""),
        "task": tasks[0] if tasks else "",
    }
    return values


def build_questions(demand: dict[str, Any], *, include_form: bool = True) -> list[QuestionSpec]:
    values = slot_values(demand)
    specs: list[QuestionSpec] = []
    for slot, template in SLOT_TEMPLATES.items():
        text = values.get(slot) or ""
        available = bool(text)
        if slot == "exploration":
            hint = f"参考线索：{text}" if text else ""
            rendered = template.format(text=hint)
        else:
            rendered = template.format(text=text or FALLBACK[slot])
        specs.append(
            QuestionSpec(
                name=slot,
                kind="noul",
                instructions=rendered,
                tag="direction",
                available=available,
            )
        )
    specs += [
        QuestionSpec(name=name, kind="noul", instructions=text, tag="policy")
        for name, text in POLICY_VALUES.items()
    ]
    specs += [
        QuestionSpec(name=name, kind="noul", instructions=text, tag="quality")
        for name, text in QUALITY.items()
    ]
    if include_form:
        specs.append(
            QuestionSpec(
                name="form",
                kind="choice",
                instructions="这条内容的主体形态是什么？只判断形态，不判断好坏。",
                criteria=dict(FORM_CRITERIA),
                tag="form",
            )
        )
        specs.append(
            QuestionSpec(name="recruit", kind="noul", instructions=RECRUIT_QUESTION, tag="form")
        )
    return specs


def to_engine_questions(specs: list[QuestionSpec]):
    import sys

    from capabilities.adapters.models.decider import TF5_DIR

    if str(TF5_DIR) not in sys.path:
        sys.path.insert(0, str(TF5_DIR))
    from strands_decider import schema

    payload = {}
    for spec in specs:
        if spec.kind == "noul":
            payload[spec.name] = schema.NoulQuestion(instructions=spec.instructions)
        else:
            payload[spec.name] = schema.ChoiceQuestion(
                instructions=spec.instructions, criteria=spec.criteria
            )
    return payload
