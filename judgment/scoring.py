"""把判断结果折算成一个可排序的分数，并按政策 §3.8 选 5+2。

分数是**可解释**的：价值取最高一项（政策说"至少满足一项"），
质量取平均（§2.1 的两条），形态只做降权（§2.3 "减少但不绝对封禁"）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 产品页与活动页也要降权：标注人的原话是「产品基本上不要推」「活动报名一律不要」。
# 这不是审美问题，是形态问题——它们讲的是某个东西存在，不是某个东西怎么成立。
REDUCED_FORMS = {"RECRUIT", "MARKETING", "PRODUCT", "EVENT"}

# 产品公告里有一类不该降权：**带完整基准与定价的模型发布**。
# 用户 2026-10-20 明确要求「模型发布要的」。判据必须是可复算的文本特征，
# 不能是"看起来像发布"——所以要求同时出现基准/价格/版本号里的两项。
_BENCH = re.compile(
    r"(bench|benchmark|基准|评测|hle|swe-?bench|terminal-?bench|mmlu|gpqa|arena|pass@|代码通过率)",
    re.I,
)
_PRICE = re.compile(r"(api\s*价格|价格不变|定价|price|pricing|元/百万|\$/m|per million)", re.I)
_NUMBERED = re.compile(r"\b\d+(?:\.\d+)?\b")
_VERSION = re.compile(r"\b[vV]?\d+\.\d+(?:[.-][A-Za-z0-9]+)*\b")

# 形态上就没有信息量的东西：版本号发布、单条引语、工具小更新、日历清单。
# 这些不该靠模型去"感觉到低密度"——前缀规律太明显了，确定性规则更可靠。
_THIN_TITLE = re.compile(
    r"^\s*(release|tool|a quote from|upcoming|snippets|changelog|weekly|newsletter)\b", re.I
)
_THIN_ZH = re.compile(r"(日历|日程表|活动预告|招募|招聘|报名)")
_VERSION_ONLY = re.compile(r"\bv?\d+\.\d+[0-9a-z.]*\s*$", re.I)
# 这两类形态不该出现在任何通道里（政策 §2.3 + AGENT.md ①）
HARD_DROP_FORMS = {"RECRUIT", "EVENT"}


def thin_content(title: str, text: str) -> str | None:
    """返回"为什么这条没有实质内容"，没有则 None。"""
    headline = (title or "").strip()
    body = (text or "").strip()
    if _THIN_TITLE.search(headline):
        return "RELEASE_NOTE"
    if _THIN_ZH.search(headline):
        return "LIST_OR_EVENT"
    if _VERSION_ONLY.search(headline) and len(headline) < 40:
        return "VERSION_BUMP"
    if len(body) < 300:
        return "TOO_SHORT"
    return None


def technical_release(text: str) -> bool:
    """模型发布算不算技术实质。

    三条里中两条才算：给出基准数字、给出定价或价格变化、给出明确版本号。
    只写"震撼发布""吊打一切"的不算——那样的公告还是产品公告。
    """
    body = text or ""
    signals = 0
    if _BENCH.search(body) and len(_NUMBERED.findall(body)) >= 3:
        signals += 1
    if _PRICE.search(body):
        signals += 1
    if len(set(_VERSION.findall(body))) >= 2:
        signals += 1
    return signals >= 2

# 与 judgment/questions.py 的固定结构对应：四个方向槽 + 四项政策价值
DIRECTION_SLOTS = {"primary", "secondary", "exploration", "task"}
POLICY_SLOTS = {
    "value_task",
    "value_capability",
    "value_decision",
    "value_exploration",
    "value_environment",
}
VALUE_SLOTS = DIRECTION_SLOTS | POLICY_SLOTS


@dataclass(frozen=True)
class Weights:
    direction: float = 0.30
    value: float = 0.30
    quality: float = 0.40
    # 探索位不只是"绕过方向门槛"，它还要有正向拉力：
    # 否则用户说"太窄了"时，参数里没有任何东西可以上调。
    exploration: float = 0.0
    reduced: float = 0.15
    recruit_threshold: float = 0.5
    direction_floor: float = 0.25
    # 时效**只降权、不加分**：政策 §4.2 明写"不因内容新鲜就提高优先级"，
    # 但"内容过时"是另一件事，它应该减分。这两个方向不能混。
    stale_days: float = 14.0
    stale_full_days: float = 60.0
    stale_penalty: float = 0.08
    # 间接相关：领域不是独立隔间。一条内容即使不直接服务当前方向，只要它改变的是
    # 上游环境（环境价值高），也会经由别的路径影响判断——AI 发新模型会改变安全的
    # 攻防面，就是这个形状。这里给它一个**下限**，不是加分：仍然要过方向门槛。
    indirect_environment: float = 0.80
    indirect_direction: float = 0.55
    indirect_min_core: float = 0.10


DEFAULT_WEIGHTS = Weights()


def summarise(
    scores: dict[str, dict[str, float]],
    weights: Weights = DEFAULT_WEIGHTS,
    *,
    age_days: float | None = None,
    text: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """scores: {question_name: {"noul": 0.7} 或 {"probabilities": {...}}}，外加 form/recruit。"""
    value_keys = [name for name in scores if name in VALUE_SLOTS]
    values = {name: float(scores[name]["noul"]) for name in value_keys if "noul" in scores[name]}
    direction_max = max((values.get(name, 0.0) for name in DIRECTION_SLOTS), default=0.0)
    policy_max = max((values.get(name, 0.0) for name in POLICY_SLOTS), default=0.0)
    # 政策 §2 的五项价值是"是否值得进来"的唯一依据；方向槽只决定"服务哪个方向"
    value_max = policy_max

    quality_parts = [
        float(scores[name]["noul"])
        for name in ("quality_testable", "quality_increment")
        if name in scores and "noul" in scores[name]
    ]
    quality = sum(quality_parts) / len(quality_parts) if quality_parts else 0.0

    # 过时降权：age_days 为 None 表示时间未知。**未知不降权**——
    # 把"不知道"当成"很旧"和当成"很新"一样是编造。未知只做标记，交给终选的人看。
    stale = 0.0
    if age_days is not None and age_days > weights.stale_days:
        span = max(1e-6, weights.stale_full_days - weights.stale_days)
        ratio = min(1.0, (age_days - weights.stale_days) / span)
        stale = round(weights.stale_penalty * ratio, 4)

    form_probabilities = (scores.get("form") or {}).get("probabilities") or {}
    form = max(form_probabilities, key=form_probabilities.get) if form_probabilities else "UNKNOWN"
    recruit = float((scores.get("recruit") or {}).get("noul", 0.0))

    if form == "PRODUCT" and technical_release(text):
        # 例外要留痕：降权被撤销这件事必须能从结果里看出来
        release_exception = True
    else:
        release_exception = False
    reduced = (form in REDUCED_FORMS and not release_exception) or recruit >= weights.recruit_threshold
    # 方向相关度是硬门槛：与该用户任何方向都不沾边的内容，不该因为"泛价值"高就被推荐。
    # 但探索通道例外——它本来就是"暂时不属于任何方向"的入口，否则破不了信息茧房。
    exploration_slot = float((scores.get("exploration") or {}).get("noul", 0.0))
    core_direction = max(
        (
            float((scores.get(name) or {}).get("noul", 0.0))
            for name in ("primary", "secondary", "task")
        ),
        default=0.0,
    )
    passes_direction = core_direction >= weights.direction_floor or exploration_slot >= 0.5
    # 间接相关：环境价值很高、且与当前方向尚有一丝关联时，方向分按"下游会受影响"计。
    # 只抬到固定下限（默认 0.55），不因为环境价值高就无限加分。
    environment = float(values.get("value_environment", 0.0))
    indirect = (
        not reduced
        and environment >= weights.indirect_environment
        and core_direction >= weights.indirect_min_core
    )
    direction_used = max(direction_max, weights.indirect_direction) if indirect else direction_max

    if not passes_direction:
        score = 0.0
    else:
        score = (
            weights.direction * direction_used
            + weights.value * value_max
            + weights.quality * quality
            + weights.exploration * exploration_slot
            - (weights.reduced if reduced else 0.0)
            - stale
        )

    # 硬性排除：招聘/活动通告、以及形态上就没有信息量的东西。
    # 它们不是"分数低"，是"不该出现在任何通道里"——分数压低还能靠别的维度救回来，
    # 这两类救不回来。
    hard = None
    if form in HARD_DROP_FORMS:
        hard = form
    elif text is not None and thin_content(title or "", text):
        hard = thin_content(title or "", text)
    if hard:
        score = 0.0
    return {
        "value_max": round(value_max, 4),
        "direction_max": round(direction_max, 4),
        "direction_used": round(direction_used, 4),
        "indirect": indirect,
        "policy_max": round(policy_max, 4),
        "quality": round(quality, 4),
        "exploration": round(exploration_slot, 4),
        # 与本领域的关联度：跨领域内容要靠这个排优先级，而不是靠"有多陌生"
        "relatedness": round(core_direction, 4),
        "form": form,
        "form_confidence": round(max(form_probabilities.values(), default=0.0), 4),
        "recruit": round(recruit, 4),
        "reduced": reduced,
        "release_exception": release_exception,
        "hard_drop": hard,
        "age_days": age_days,
        "recency_penalty": stale,
        "score": round(score, 4),
        "values": {key: round(value, 4) for key, value in values.items()},
    }


def rank(items: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    """按分数降序；同分时降权项排后，再按标题保持稳定顺序。"""
    # 最后一个键必须是 item_id：只按分数和标题排时，两条同分同名的条目
    # 顺序由输入顺序决定，同一批数据换个顺序就换一个结果。
    ordered = sorted(
        items,
        key=lambda item: (-item["score"], item["reduced"], item.get("title", ""), item.get("item_id", "")),
    )
    return ordered[:limit] if limit else ordered


def select(
    ranked: list[dict[str, Any]],
    core: int = 5,
    exploration: int = 2,
    *,
    exploration_threshold: float = 0.5,
    threshold: float = 0.0,
    max_per_domain: int = 2,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """按 §3.8 选核心 5 + 探索 2。

    探索位优先给"探索价值最高"的条目，让跨领域内容不会被核心位挤掉。

    threshold 是"够不够格被推荐"的线：低于它的条目不进候选池。
    这条线必须真的参与选择，否则调阈值只是在改一个没人用的数字——
    用户说"太水了"之后参数动了，结果一模一样。
    """
    ordered = list(ranked)
    remaining = [item for item in ordered if item["score"] >= threshold] if threshold > 0 else ordered
    dropped = [item for item in ordered if all(item is not kept for kept in remaining)]
    core_items = _pick_core(remaining, core=core, max_per_domain=max_per_domain)
    picked_core = {id(item) for item in core_items}
    remaining = [item for item in remaining if id(item) not in picked_core]

    # 探索位专给"跨领域"条目：按 exploration 槽本身的分数取，而不是按综合分。
    # 降权项（§2.3）不占探索位。
    def exploration_slot(item: dict[str, Any]) -> float:
        return float((item.get("values") or {}).get("exploration", 0.0))

    eligible = [
        item
        for item in remaining
        if not item.get("reduced") and exploration_slot(item) >= exploration_threshold
    ]

    def exploration_priority(item: dict[str, Any]) -> float:
        """跨领域位不是"越陌生越好"。

        用户的原话：跨领域必须和自己正在做的领域**有关联**，关联度越高优先级越高。
        所以探索分打底、关联度加权：同样陌生，更贴近本领域的先上。
        """
        relatedness = float(item.get("relatedness", 0.0))
        return exploration_slot(item) * (0.5 + relatedness)

    exploration_items = sorted(eligible, key=exploration_priority, reverse=True)[:exploration]
    # 用 item_id 判等：不同来源的标题完全可能撞车，撞一次就会凭空多推荐一条
    def key(item: dict[str, Any]) -> Any:
        return item.get("item_id") or item.get("title")

    chosen_ids = {key(item) for item in core_items + exploration_items}
    rest = [item for item in remaining if key(item) not in chosen_ids]

    # Agent 可以覆盖打分结果，但必须留下理由；覆盖记录随结果一起返回，
    # 这样"为什么不是分数最高的那几条"永远有据可查。
    applied: list[dict[str, Any]] = []
    if overrides:
        for item in list(core_items) + list(exploration_items) + list(rest):
            rule = overrides.get(item.get("item_id") or item.get("title"))
            if not rule:
                continue
            if rule.get("include") is False and item in core_items:
                core_items.remove(item)
                rest.append(item)
            elif rule.get("include") is True and item in rest:
                rest.remove(item)
                if len(core_items) < core:
                    core_items.append(item)
                else:
                    exploration_items.append(item)
            applied.append(
                {
                    "item_id": key(item),
                    "include": bool(rule.get("include")),
                    "reason": rule.get("reason", ""),
                    "was_selected": item in core_items or item in exploration_items,
                }
            )

    return {
        "core": core_items,
        "exploration": exploration_items,
        "rest": rest,
        "dropped": dropped,
        "overrides": applied,
    }


def _top_direction(item: dict[str, Any]) -> str:
    """这条内容最贴近哪个方向槽，用来决定它该占核心位还是探索位。"""
    values = item.get("values") or {}
    slots = ("primary", "secondary", "task")
    return max(slots, key=lambda slot: float(values.get(slot, 0.0)))

def _pick_core(
    candidates: list[dict[str, Any]],
    *,
    core: int,
    max_per_domain: int,
    secondary_max: int = 2,
) -> list[dict[str, Any]]:
    """挑核心位：主目标/当前任务优先，示例第二方向最多占 2 席，且必须过分数线。

    标注人 2026-10-19 定的规则：

    - 「当前任务大于长期目标」，所以主方向先占位；
    - 示例第二方向**不预先留席位**：「不管分多低都必须留一位，这肯定是不行的」；
    - 但示例第二方向一旦过线就上，**上限 2 条，可以是 0 条**。

    也就是说：示例第二方向和别的内容一样排队，只是名额单独封顶。
    """
    secondary_used = 0
    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    preferred = [i for i in candidates if _top_direction(i) in ("primary", "task")]
    others = [i for i in candidates if _top_direction(i) not in ("primary", "task")]

    for item in preferred + others:
        if len(picked) >= core:
            break
        is_secondary = _top_direction(item) == "secondary"
        if is_secondary and secondary_used >= secondary_max:
            continue
        domain = str(item.get("domain") or "")
        if domain and counts.get(domain, 0) >= max_per_domain:
            continue
        picked.append(item)
        if is_secondary:
            secondary_used += 1
        if domain:
            counts[domain] = counts.get(domain, 0) + 1
    return picked
