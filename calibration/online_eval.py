"""验证"先验 + 在线后训练"对不同用户都能收敛。

说清楚这个实验证明了什么、没证明什么：

证明：在同样的 13 个问句与同一批真实内容特征下，不同偏好的用户
      通过少量反馈，参数会朝"更符合他"的方向移动，且不会一步跳过界。

没证明：内容特征本身没被检验。这里给每条内容打分的仍是真实模型的输出，
      但"用户想要什么"是按 13 个问句线性组合出来的（合成偏好）。
      所以这是对**调参机制**的检验，不是对**测量质量**的检验。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.adaptive import (  # noqa: E402
    FeedbackEvent,
    append_pending,
    commit,
    load_ledger,
    load_pending,
    propose,
    record_ledger,
)
from judgment.params import PRIOR, ParameterStore, Parameters  # noqa: E402
from judgment.scoring import rank, select, summarise  # noqa: E402

# ---------------------------------------------------------------------------
# 两组模拟用户，故意分开：
#
# A 组（COMPONENT）：偏好恰好落在参数族里——"我更看重方向/价值/质量/探索"。
#    这组用来检验**调参机制本身**能不能收敛。它应当明显变好。
# B 组（HIDDEN）：偏好落在具体问句上（"我只关心主目标和技术可检验性"），
#    参数族表达不了。这组用来界定这套机制的**能力边界**。
#    它不该变好；如果它变好了，反而说明模拟写错了。
# ---------------------------------------------------------------------------

COMPONENT: dict[str, dict[str, float]] = {
    "quality_first": {"quality": 0.60, "value": 0.25, "direction": 0.15, "exploration": 0.0},
    "direction_first": {"quality": 0.20, "value": 0.20, "direction": 0.60, "exploration": 0.0},
    "curiosity": {"quality": 0.20, "value": 0.20, "direction": 0.15, "exploration": 0.45},
}

# B 组：权重是"隐藏偏好"，模拟器自己知道，系统看不见。
HIDDEN: dict[str, dict[str, float]] = {
    "tech_deep": {
        "primary": 1.2, "quality_testable": 0.8, "value_capability": 0.5,
        "form:TECHNICAL": 0.3, "form:MARKETING": -0.9, "form:RECRUIT": -1.0,
    },
    "visual_narrative": {
        "secondary": 1.2, "value_exploration": 0.7, "quality_increment": 0.6,
        "form:MARKETING": -0.5,
    },
    "explorer": {
        "exploration": 1.4, "value_exploration": 1.0, "quality_increment": 0.5, "primary": 0.2,
    },
    "pragmatic": {
        "task": 1.2, "value_decision": 0.9, "quality_testable": 0.5, "form:RECRUIT": -1.0,
    },
}


def utility(item: dict[str, Any], hidden: dict[str, float]) -> float:
    scores = item["scores"]
    total = 0.0
    for key, weight in hidden.items():
        if key.startswith("form:"):
            total += weight * float((scores.get("form") or {}).get("probabilities", {}).get(key[5:], 0.0))
        else:
            total += weight * float((scores.get(key) or {}).get("noul", 0.0))
    return total


def component_utility(item: dict[str, Any], weights: dict[str, float]) -> float:
    scores = item["scores"]
    from judgment.scoring import summarise

    summary = summarise(scores)
    return (
        weights.get("direction", 0.0) * summary["direction_max"]
        + weights.get("value", 0.0) * summary["policy_max"]
        + weights.get("quality", 0.0) * summary["quality"]
        + weights.get("exploration", 0.0) * summary["exploration"]
    )


def label_items(
    items: list[dict[str, Any]],
    hidden: dict[str, float],
    ratio: float = 0.25,
    *,
    mode: str = "raw",
) -> dict[str, int]:
    """把"用户真正想要"定义成隐藏效用的前 25%。"""
    score = component_utility if mode == "component" else utility
    scored = sorted(items, key=lambda item: -score(item, hidden))
    cut = max(1, int(len(scored) * ratio))
    chosen = {item["item_id"] for item in scored[:cut]}
    return {item["item_id"]: (1 if item["item_id"] in chosen else 0) for item in items}


def score_all(items: list[dict[str, Any]], parameters: Parameters) -> list[dict[str, Any]]:
    weights = parameters.to_weights()
    summarised = []
    for item in items:
        summary = summarise(item["scores"], weights)
        summary["item_id"] = item["item_id"]
        summary["title"] = (item.get("provenance") or {}).get("title") or item["item_id"]
        summarised.append(summary)
    return summarised


def _event(kind: str, item: dict[str, Any], *, at: str, delivered: bool) -> FeedbackEvent:
    summary = {key: item[key] for key in ("direction_max", "policy_max", "quality", "exploration", "reduced", "score", "form")}
    return FeedbackEvent(
        event_id=f"{kind}-{item['item_id']}-{at}",
        at=at,
        kind=kind,
        item_id=item["item_id"],
        summary=summary,
        delivered=delivered,
    )


@dataclass
class RoundLog:
    round: int
    delivered: list[str]
    precision: float
    commits: int
    parameters: dict[str, float]


@dataclass
class TypeResult:
    name: str
    rounds: list[RoundLog] = field(default_factory=list)
    final: dict[str, float] = field(default_factory=dict)
    commits: int = 0

    def summary(self) -> dict[str, Any]:
        if not self.rounds:
            return {"type": self.name}
        first = self.rounds[0].precision
        tail = [log.precision for log in self.rounds[-2:]]
        return {
            "type": self.name,
            "prior_precision": round(first, 4),
            "adapted_precision": round(statistics.fmean(tail), 4),
            "delta": round(statistics.fmean(tail) - first, 4),
            "commits": self.commits,
            "final_parameters": self.final,
            "per_round": [round(log.precision, 4) for log in self.rounds],
        }


def run_type(
    name: str,
    items: list[dict[str, Any]],
    labels: dict[str, int],
    *,
    rounds: int = 6,
    base: Path,
    now_stamp: str = "2026-10-18T12:00:00+00:00",
    fresh: bool = True,
) -> TypeResult:
    import shutil
    from datetime import datetime, timedelta

    store = ParameterStore(base, "sim-" + name)
    if fresh and store.root.exists():
        # 每个模拟用户必须从先验开始：复用上次的目录会让"调后"变成"上次调过的起点"
        shutil.rmtree(store.root)
    result = TypeResult(name=name)
    for round_index in range(1, rounds + 1):
        parameters = store.current().parameters
        ranked = rank(score_all(items, parameters))
        picked = select(ranked, threshold=parameters.threshold)
        delivered = [item["item_id"] for item in picked["core"] + picked["exploration"]]
        precision = sum(labels[item_id] for item_id in delivered) / max(1, len(delivered))
        result.rounds.append(
            RoundLog(round_index, delivered, precision, store.current().version, parameters.as_dict())
        )

        by_id = {item["item_id"]: item for item in ranked}
        at = (datetime.fromisoformat(now_stamp) + timedelta(days=round_index)).isoformat(timespec="seconds")
        vetoed = [by_id[item_id] for item_id in delivered if labels[item_id] == 0][:3]
        # 用户提出"这条我要"的两种情况：排名靠前但没挤进去，以及被门槛直接过滤掉。
        # 只取前者，模拟就永远测不到"门槛把人要的东西挡在门外"这类反馈。
        wanted = [
            item for item in ranked if item["item_id"] not in set(delivered) and labels[item["item_id"]] == 1
        ]
        promoted = wanted[:1] + [item for item in wanted if item["score"] == 0.0][:1]
        seen: set[str] = set()
        promoted = [item for item in promoted if not (item["item_id"] in seen or seen.add(item["item_id"]))]

        ledger_rows = [
            {
                "item_id": item["item_id"],
                "verdict": "DISLIKED" if labels[item["item_id"]] == 0 else "LIKED",
                "direction_max": item["direction_max"],
                "policy_max": item["policy_max"],
                "quality": item["quality"],
                "exploration": item["exploration"],
                "reduced": item["reduced"],
                "score": item["score"],
                "delivered": item["item_id"] in set(delivered),
                "recorded_at": at,
            }
            for item in vetoed + promoted
        ]
        if ledger_rows:
            record_ledger(store, ledger_rows)

        events = [_event("ITEM_VETO", item, at=at, delivered=True) for item in vetoed]
        events += [_event("ITEM_PROMOTE", item, at=at, delivered=False) for item in promoted]
        if not events:
            break
        # 真实用户不会一轮就给够三条反馈：没够数的先排队，够数了再一起提交
        append_pending(store, events)
        proposal = propose(store, load_pending(store), ledger=load_ledger(store), now=datetime.fromisoformat(at))
        if proposal.accepted:
            commit(store, proposal, now=datetime.fromisoformat(at))
            result.commits += 1

    result.final = store.current().parameters.as_dict()
    return result


def run(scores_path: Path, *, rounds: int = 6, base: Path | None = None, types: list[str] | None = None) -> dict[str, Any]:
    items = [json.loads(line) for line in scores_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not items:
        raise SystemExit("打分行是空的")
    base = base or (ROOT / "state" / "sim")
    groups = {
        "in_family": list(COMPONENT),
        "out_of_family": list(HIDDEN),
    }
    if types:
        groups = {name: [t for t in members if t in types] for name, members in groups.items()}
        groups = {name: members for name, members in groups.items() if members}

    results: dict[str, list[dict[str, Any]]] = {}
    for group, names in groups.items():
        rows = []
        for name in names:
            mode = "component" if group == "in_family" else "raw"
            labels = label_items(items, COMPONENT[name] if mode == "component" else HIDDEN[name], mode=mode)
            rows.append(run_type(name, items, labels, rounds=rounds, base=base).summary())
        results[group] = rows

    flattened = [row for rows in results.values() for row in rows if "delta" in row]
    return {
        "scores_path": str(scores_path),
        "items": len(items),
        "rounds": rounds,
        "groups": results,
        "mean_delta": round(statistics.fmean([row["delta"] for row in flattened]), 4) if flattened else 0.0,
        "prior_parameters": Parameters(**PRIOR).as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="多用户在线调参模拟")
    parser.add_argument("--scores", default="calibration/scores-crawl-ai.jsonl")
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--out", default="calibration/online-eval.json")
    args = parser.parse_args(argv)

    result = run(Path(args.scores), rounds=args.rounds)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    for group, rows in result["groups"].items():
        print(f"[{group}]")
        for item in rows:
            print(
                f"  {item['type']:16s} 先验={item['prior_precision']:.3f} 调后={item['adapted_precision']:.3f} "
                f"Δ={item['delta']:+.3f} 提交={item['commits']} 每轮={item['per_round']}"
            )
    print(f"均值 Δ={result['mean_delta']:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
