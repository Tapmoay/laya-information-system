"""两级筛选：粗选 → 精选 → Agent 终选（5+2）。

为什么要两级：全量内容太长，判断模型看 3500 字里前半段是导航和铺垫，
信噪比低；而候选池里真正需要"细看"的只有十几条。
所以第一级用便宜的全量判断做粗筛，第二级把少数几条压成摘要再看一遍。

为什么第二级仍然用同一套 13 个问句：换一套问句就等于换了尺子，
两次打分就无法互相校验；而且新问句没有经过任何验证。
同一把尺子量两遍，一致 = 信号强，不一致 = 该交给人看。

不变量：这两级都属于**测量**，最终 5+2 的选择仍由确定性代码 + Agent 完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from judgment.scoring import Weights, rank, summarise
from judgment.text import summarise as summarise_text

# 第二级只判"值不值"，不判"对不对"：方向已经在第一级用全文判过了。
# 拿 320 字的摘要去问方向，会把方向判成 0（模型看不出来），
# 于是分数被门槛直接清零——那不是"内容不好"，是"问错了问题"。
REFINE_WEIGHTS = Weights(
    direction=0.0, value=0.5, quality=0.5, exploration=0.0, reduced=0.10, direction_floor=0.0
)

PASS = "PASS"
DISAGREE = "DISAGREE"
DROP = "DROP"


@dataclass
class StageItem:
    item_id: str
    title: str
    coarse_score: float
    summary: str = ""
    refined_score: float | None = None
    verdict: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "coarse_score": round(self.coarse_score, 4),
            "summary": self.summary,
            "refined_score": None if self.refined_score is None else round(self.refined_score, 4),
            "verdict": self.verdict,
            "note": self.note,
        }


@dataclass
class FunnelResult:
    coarse_threshold: float
    refine_threshold: float
    passed_coarse: list[StageItem] = field(default_factory=list)
    refined: list[StageItem] = field(default_factory=list)
    dropped: list[StageItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "coarse_threshold": self.coarse_threshold,
            "refine_threshold": self.refine_threshold,
            "counts": {
                "passed_coarse": len(self.passed_coarse),
                "refined": len(self.refined),
                "dropped": len(self.dropped),
                "pass": sum(1 for item in self.refined if item.verdict == PASS),
                "disagree": sum(1 for item in self.refined if item.verdict == DISAGREE),
            },
            "items": [item.as_dict() for item in self.refined],
            "dropped": [item.as_dict() for item in self.dropped],
        }


def coarse(
    scored: Sequence[dict[str, Any]],
    *,
    threshold: float,
    limit: int,
    weights: Weights | None = None,
) -> FunnelResult:
    """第一级：全量打分 → 过线 → 截断到 limit 条。

    截断是必须的：第二级每条要跑一次模型，候选池不设上限就等于取消了漏斗。
    """
    threshold_values = []
    missed = []
    for row in scored:
        score = summarise(row["scores"], weights or Weights())["score"]
        item = StageItem(
            item_id=row["item_id"],
            title=(row.get("provenance") or {}).get("title", ""),
            coarse_score=score,
        )
        if score >= threshold:
            threshold_values.append(item)
        else:
            item.verdict = DROP
            item.note = "粗选未过线"
            missed.append(item)
    threshold_values.sort(key=lambda item: -item.coarse_score)
    passed = threshold_values[:limit]
    overflow = threshold_values[limit:]
    for item in overflow:
        item.verdict = DROP
        item.note = "粗选过线但超出候选池上限"
    return FunnelResult(
        coarse_threshold=threshold,
        refine_threshold=threshold,
        passed_coarse=passed,
        dropped=missed + overflow,
    )


def refine(
    result: FunnelResult,
    *,
    ask: Callable[[str], dict[str, Any]],
    bodies: dict[str, str],
    threshold: float,
    weights: Weights | None = None,
    summary_chars: int = 320,
) -> FunnelResult:
    """第二级：把候选压成摘要，再用同一套问句打一次分。

    ask(text) 负责把"一段文本"变成 13 个问句的答案；这里不关心它背后是本地模型还是远端。
    """
    for item in result.passed_coarse:
        body = bodies.get(item.item_id, "")
        item.summary = summarise_text(body, item.title, summary_chars)
        if not item.summary:
            item.refined_score = None
            item.verdict = DISAGREE
            item.note = "没有可用摘要，无法复判"
            continue
        scores = ask(item.summary)
        item.refined_score = summarise(scores, weights or REFINE_WEIGHTS)["score"]
        if item.coarse_score >= threshold and item.refined_score >= threshold:
            item.verdict = PASS
            item.note = "全量与摘要两次判断一致"
        elif item.refined_score < threshold:
            item.verdict = DISAGREE
            item.note = "摘要判断低于阈值：可能是长文噪声拉高了第一遍分数"
        else:
            item.verdict = PASS
    result.refined = list(result.passed_coarse)
    result.refine_threshold = threshold
    return result


def finalize(
    result: FunnelResult,
    *,
    core: int = 5,
    exploration: int = 2,
    exploration_threshold: float = 0.5,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """最后一步：只从 PASS 里按分数取 5+2；DISAGREE 的交给人/Agent。"""
    candidates = [
        {
            "item_id": item.item_id,
            "title": item.title,
            "score": item.refined_score if item.refined_score is not None else item.coarse_score,
            "reduced": False,
            "values": {},
        }
        for item in result.refined
        if item.verdict == PASS
    ]
    ordered = sorted(candidates, key=lambda row: -row["score"])
    chosen = ordered[:core]
    review = [item.as_dict() for item in result.refined if item.verdict == DISAGREE]
    return {
        "core": chosen,
        "exploration": [],
        "needs_review": review,
        "capacity": {"core": core, "exploration": exploration},
        "overrides": overrides or {},
    }
