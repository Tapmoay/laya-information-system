"""三条通道：目标驱动 / 变化监测 / 受控探索。

这三条不是"三个分类标签"，是三种**工作方式**，所以它们的筛选标准不一样：

| 通道 | 要解决的问题 | 判据偏重 |
|---|---|---|
| A 目标驱动 | 把正在做的事推进下去 | 任务价值、主方向、能不能用 |
| B 变化监测 | 别漏掉会影响未来判断的外部变化 | 环境价值，**即使现在用不上也留** |
| C 受控探索 | 扩大"我不知道自己需要什么"的接触面 | 探索价值、审美与表达 |

为什么必须分开：同一个分数线上，A 要的是"能立刻用"，B 要的是"重要但现在用不上"，
C 要的是"陌生但可能有启发"。用一把尺子量，B 和 C 一定被 A 挤掉——
这不是因为它们不重要，是因为它们不"急"。

分配是确定性的：在三个通道各自的得分里取最大；平手时 A > B > C（能用优先）。
"""

from __future__ import annotations

from typing import Any

CHANNELS = {
    "A": "目标驱动：推进当前项目与明确问题",
    "B": "变化监测：可能影响未来判断的外部变化",
    "C": "受控探索：陌生领域、审美与表达",
}

# 三态：值得深入 / 值得暂存 / 直接放弃
DEEP = "DEEP"
SAVE = "SAVE"
DROP = "DROP"

STATE_LABELS = {
    DEEP: "值得深入",
    SAVE: "值得暂存",
    DROP: "直接放弃",
}

# 平手优先顺序：能用优先，其次"别漏掉变化"，最后才是探索
_TIE_ORDER = ("A", "B", "C")


COMMERCIAL = (
    "品牌", "营销", "广告", "投放", "转化", "销量", "联名", "代言", "发布会",
    "campaign", "brand", "marketing", "advertis", "launch event", "promotion",
)


# 有这些词，说明它在讲"怎么做出来的"，而不是"我们发了什么"
CRAFT = (
    "原理", "机制", "结构", "构图", "镜头", "光影", "材质", "工艺", "比例", "节奏",
    "气流", "下压力", "加速度", "参数", "technique", "how it works", "mechanism",
    "composition", "lighting", "material", "geometry",
)
NUMERIC = __import__("re").compile(r"\b\d+(?:\.\d+)?\s*(?:kg|g|mm|cm|m|s|fps|hz|gpu|cpu|%|倍|米|公斤)?\b")


def commercial_intent(text: str | None) -> bool:
    """只是一个**标记**，不用来剔除任何内容。

    判断是否存在商业意图，用来在产物里标注"这条是商业内容"，
    让人自己决定要不要在意。剔不剔除由人决定，不由一个词表决定。
    """
    body = (text or "").lower()
    if not body:
        return False
    return sum(1 for word in COMMERCIAL if word.lower() in body) >= 3


def assign(
    values: dict[str, float] | None,
    *,
    form: str | None = None,
    text: str | None = None,
    secondary_floor: float = 0.55,
) -> dict[str, Any]:
    """按测量值分配通道。

    values 是 13 问的槽位分（primary / secondary / exploration / task /
    value_task / value_environment ...）。缺项按 0 处理，不抛异常——
    测量缺失不该让交付环节崩掉。
    """
    v = values or {}
    scores = {
        "A": max(float(v.get("task") or 0.0), float(v.get("primary") or 0.0)),
        "B": float(v.get("value_environment") or 0.0),
        "C": max(float(v.get("exploration") or 0.0), float(v.get("secondary") or 0.0)),
    }
    # 审美与表达：次方向够高时，C 通道额外加权，避免被"陌生领域"那一项稀释
    if float(v.get("secondary") or 0.0) >= secondary_floor:
        scores["C"] = max(scores["C"], float(v.get("secondary") or 0.0))
    # C 通道**不按形态排除**。用户 2026-10-20：「跨领域探索的话，不要卡得太死」。
    # 实测过一刀切的代价：按 MARKETING 排除 C，建筑与设计作品也被模型标成 MARKETING，
    # C 从 18 条掉到 4 条；按"商业词 ≥3"排除，麦当劳那篇讲画面怎么搭的也被误杀。
    # 所以改成**标记而不是剔除**：商业内容照进，但在结果里带一个标记，
    # 让人看到"这条是商业内容"再自己判断。
    commercial = commercial_intent(text) or form == "MARKETING"
    best = max(scores.values())
    channel = next(c for c in _TIE_ORDER if scores[c] == best)
    return {
        "channel": channel,
        "scores": {k: round(val, 3) for k, val in scores.items()},
        "commercial": commercial,
    }


def decide(score: float, *, deep_from: float = 0.60, save_from: float = 0.40) -> str:
    """三态判定。分数只是入口，三通道共用同一组线（线以下一律放弃）。"""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return DROP
    if value >= deep_from:
        return DEEP
    if value >= save_from:
        return SAVE
    return DROP


# 分通道的门槛：同一条线对三条通道不公平。
# A/B 要"能立刻用"或"重要"，线该高；C 里的作品与设计内容分数结构性偏低
# （它们不解决问题、也不讲机制），用同一条线会把示例第二方向整条通道清空——
# 实测把线提到 0.50 后，C 通道从 18 条掉到 4 条。
THRESHOLDS = {"A": 0.50, "B": 0.50, "C": 0.42}


def threshold_for(channel: str) -> float:
    return THRESHOLDS.get(channel, 0.50)


def label(state: str) -> str:
    return STATE_LABELS.get(state, state)
