"""Agent 终选：模型只给概率与粗筛，最后 5+2 由 Agent 定，并且必须给理由。

为什么终选要单独一层，而不是把方向词写窄一点：
「不要建筑/室内作品展示」这种负向枚举永远列不全——排掉建筑还有室内，
排掉室内还有装置、家具、舞台。负向清单越写越长，最后变成一份没人维护的规则。
正确的分工是：模型负责召回（宁宽勿窄，把可能的都放进来），
Agent 负责精确（拿着完整需求上下文，只留真的要的），理由留痕并可回灌给调参层。
"""

from __future__ import annotations

from judgment.labels import goal_labels

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from judgment.scoring import summarise

SLOT_LABELS = {
    "primary": "主目标",
    "secondary": "第二方向",
    "exploration": "探索",
    "task": "当前任务",
}


def _demand_lines(demand: dict[str, Any]) -> list[str]:
    from judgment.questions import slot_values

    slots = slot_values(demand)
    lines = []
    for key in ("primary", "secondary", "exploration", "task"):
        lines.append("- **{}**：{}".format(SLOT_LABELS[key], slots.get(key) or "（空）"))
    constraints = demand.get("constraints") or {}
    if constraints:
        lines.append(
            "- **约束**：每日 {} 分钟；能量状态 {}".format(
                constraints.get("daily_information_budget_minutes", "?"),
                constraints.get("energy_state", "?"),
            )
        )
    return lines


def _signals(scores: dict[str, Any]) -> str:
    summary = summarise(scores)
    values = summary.get("values") or {}
    top_name, top_value = max(values.items(), key=lambda kv: kv[1]) if values else ("-", 0.0)
    return (
        "方向 primary {:.2f} / secondary {:.2f} / 探索 {:.2f} / 任务 {:.2f} ｜ ".format(
            float(values.get("primary", 0.0)),
            float(values.get("secondary", 0.0)),
            float(values.get("exploration", 0.0)),
            float(values.get("task", 0.0)),
        )
        + "最高价值 {} {:.2f} ｜ 质量 {:.2f} ｜ 形态 {} ｜ 招聘 {:.2f}".format(
            top_name, top_value, summary["quality"], summary["form"], summary["recruit"]
        )
    )


POLICY_HEADINGS = ("## 2. 信息的价值标准", "### 2.1", "### 2.2", "### 2.3", "### 3.7", "### 3.8")


def _policy_excerpt(text: str) -> list[str]:
    """只取判断真正用得到的几节，不把整份政策塞进包里。"""
    out: list[str] = []
    keeping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            keeping = stripped.startswith(POLICY_HEADINGS[0])
        elif stripped.startswith("### "):
            keeping = any(stripped.startswith(heading) for heading in POLICY_HEADINGS[1:])
        elif stripped.startswith("---"):
            keeping = False
        if keeping and stripped:
            out.append(line)
    return out


PRINCIPLES = (
    "主旨：不是替我把信息筛完，而是帮我建立我与信息之间可控的关系。",
    "真正要建立的，不是对所有信息的控制，而是对自己与信息之间关系的控制。",
    "成熟的信息环境 = 知道自己为什么选择 + 愿意承认不知道什么 + 按现实结果持续修正。",
    "好内容 ≠ 对你有用的内容；不是所有有价值的信息都值得保存，值得保存的也不一定值得立即阅读。",
)


def build_packet(
    candidates: Sequence[dict[str, Any]],
    demand: dict[str, Any],
    *,
    bodies: dict[str, str] | None = None,
    summary_chars: int = 320,
    core: int = 5,
    exploration: int = 2,
    policy: str = "",
) -> str:
    """给 Agent 看的终选包：需求原文 + 政策节选 + 每条的全部可判信息。

    政策必须随包送进去：让 Agent「凭记忆」判断，等于让它自己发明标准。
    """
    from judgment.text import summarise as summarise_text

    bodies = bodies or {}
    lines = ["# 终选包", "", "## 需求（原文，不改写）", ""]
    lines += _demand_lines(demand)
    # 原则不依赖政策文件：没有政策原文时它也要在，否则 agent 只看到需求就开选。
    lines += ["", "## 判断原则（比规则长寿，先看这个）", ""]
    lines += ["- " + item for item in PRINCIPLES]
    if policy.strip():
        lines += ["", "## 判断依据（政策原文节选）", ""]
        lines += [line for line in _policy_excerpt(policy)]
    lines += [
        "",
        "## 怎么选",
        "",
        "挑选最多 {} 条核心 + {} 条探索，每条都必须写理由。".format(core, exploration),
        "排除也要写理由：排除理由会作为反馈回灌给调参层。",
        "",
        "## 候选（{} 条，按粗选分排序；末尾可能带事件候选）".format(len(candidates)),
        "",
    ]
    for index, row in enumerate(candidates, 1):
        title = (row.get("provenance") or {}).get("title") or row.get("title") or ""
        lines.append("### {}. {} ｜ 粗分 {:.3f}".format(index, row["item_id"], float(row.get("score", 0.0))))
        lines.append("")
        lines.append("**" + title + "**")
        lines.append("")
        body = bodies.get(row["item_id"], "")
        text = summarise_text(body, title, summary_chars) if body else ""
        if text:
            lines.append("> " + text)
            lines.append("")
        if row.get("scores"):
            lines.append("信号：" + _signals(row["scores"]))
            lines.append("")
        labels = row.get("labels") or []
        if labels:
            lines.append("标签：" + " · ".join(labels))
            lines.append("")
        if row.get("event_slot"):
            lines.append(
                "**事件候选**：环境价值高、和主目标未必相关。它是不是「改变我对世界判断的重大事件」由你定；"
                "判是，就排在所有任务之前占核心位，并在理由里写清它改变了什么判断。"
            )
            lines.append("")
    return chr(10).join(lines)


def decision_template(candidates: Sequence[dict[str, Any]], *, core: int = 5, exploration: int = 2) -> dict[str, Any]:
    return {
        "picks": [{"item_id": "", "slot": "core", "reason": "", "evidence": "从该条摘要里原文摘一句"}],
        "excludes": [{"item_id": "", "reason": "", "evidence": "从该条摘要里原文摘一句"}],
        "limits": {"core": core, "exploration": exploration},
    }


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch not in " \t\r\n…").lower()


def check_evidence(evidence: str, summary: str) -> str | None:
    """证据必须真的出自摘要。

    光在指令里写「必须读摘要」是没用的——模型可以说自己读了。
    可验证的做法：让它引一句摘要里的原话，然后在这里比对。
    对不上就是没读，或者读了编的，一律拒绝。
    """
    quote = _norm(evidence)
    if len(quote) < 6:
        return "证据太短，无法证明读过摘要"
    if quote not in _norm(summary):
        return "证据不在该条摘要里（摘要被改过，或者没读摘要）"
    return None


@dataclass
class DecisionCheck:
    final: dict[str, Any] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def apply_decision(
    candidates: Sequence[dict[str, Any]],
    decision: dict[str, Any],
    *,
    core: int = 5,
    exploration: int = 2,
    summaries: dict[str, str] | None = None,
) -> DecisionCheck:
    """校验并落实 Agent 的选择。

    两条硬规则：理由缺失不合法（没有理由就没有回灌信号）；
    给了 summaries 时，每条还必须有出自摘要的原文证据（没有证据就是没读摘要）。
    """
    check = DecisionCheck()
    known = {row["item_id"]: row for row in candidates}
    summaries = summaries or {}

    def _require_evidence(entry: dict[str, Any], item_id: str) -> bool:
        if not summaries:
            return True
        problem = check_evidence(str(entry.get("evidence") or ""), summaries.get(item_id, ""))
        if problem:
            check.violations.append("{}：{}".format(problem, item_id))
            return False
        return True
    picks = decision.get("picks") or []
    excludes = decision.get("excludes") or []

    core_picks: list[dict[str, Any]] = []
    exploration_picks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pick in picks:
        item_id = str(pick.get("item_id") or "")
        reason = str(pick.get("reason") or "").strip()
        slot = str(pick.get("slot") or "core")
        if item_id not in known:
            check.violations.append("未知条目：" + item_id)
            continue
        if not reason:
            check.violations.append("缺少理由：" + item_id)
            continue
        if item_id in seen:
            check.violations.append("重复选择：" + item_id)
            continue
        if not _require_evidence(pick, item_id):
            continue
        seen.add(item_id)
        entry = {"item_id": item_id, "reason": reason, "score": float(known[item_id].get("score", 0.0))}
        # 目标标签是多值的：一条内容可以同时服务长期目标和当前任务。
        labels = goal_labels(known[item_id].get("values"))
        if labels:
            entry["labels"] = labels
        # 终选者读过全文写下的 gist 必须带进 selection.json，否则 final_digest
        # 拿不到它，只能退回机械摘要（页头/日期/作者都还在里面）。
        gist = str(pick.get("gist") or "").strip()
        if gist:
            entry["gist"] = gist
        if pick.get("major_event") is True:
            # 标了"重大事件"就要说得出改变了什么判断，否则它只是新闻。
            event_reason = str(pick.get("event_reason") or "").strip()
            if not event_reason:
                check.violations.append("标了重大事件却没写改变了什么：" + item_id)
                continue
            entry["major_event"] = True
            entry["event_reason"] = event_reason
        if slot == "exploration":
            exploration_picks.append(entry)
        else:
            core_picks.append(entry)

    if len(core_picks) > core:
        check.violations.append("核心位超出上限：{} > {}".format(len(core_picks), core))
    if len(exploration_picks) > exploration:
        check.violations.append("探索位超出上限：{} > {}".format(len(exploration_picks), exploration))

    excluded: list[dict[str, str]] = []
    for item in excludes:
        item_id = str(item.get("item_id") or "")
        reason = str(item.get("reason") or "").strip()
        if item_id and item_id not in known:
            check.violations.append("未知条目（排除）：" + item_id)
            continue
        if item_id in seen:
            check.violations.append("同一条既选又排：" + item_id)
            continue
        if not reason:
            check.violations.append("排除缺少理由：" + item_id)
            continue
        if not _require_evidence(item, item_id):
            continue
        excluded.append({"item_id": item_id, "reason": reason})

    check.final = {
        "core": core_picks,
        "exploration": exploration_picks,
        "excluded": excluded,
        "decided_by": "AGENT",
    }
    return check


def override_events(check: DecisionCheck, candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 Agent 的选择翻译成调参层认识的覆盖记录。"""
    known = {row["item_id"] for row in candidates}
    selected_coarse = {row["item_id"] for row in candidates if row.get("selected")}
    events: list[dict[str, Any]] = []
    for entry in check.final.get("core", []) + check.final.get("exploration", []):
        events.append(
            {
                "item_id": entry["item_id"],
                "include": True,
                "was_selected": entry["item_id"] in selected_coarse,
                "reason": entry["reason"],
            }
        )
    for entry in check.final.get("excluded", []):
        events.append(
            {
                "item_id": entry["item_id"],
                "include": False,
                "was_selected": entry["item_id"] in selected_coarse,
                "reason": entry["reason"],
            }
        )
    return [event for event in events if event["item_id"] in known]
