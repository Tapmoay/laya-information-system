"""先验 + 在线后训练：参数跟着用户走，而不是一开始就调死。

为什么是"先验 + 微调"而不是"拟合一套通用参数"：
同一批问句下，不同用户对"价值/质量/方向"的取舍本来就不同；
用一套参数去同时满足所有画像，只会在数据不足时拟合出噪声。
所以初始参数只当成先验，真正决定它的是用户自己的反馈。

三条护栏（缺一条这套机制就不该上线）：
1. 只动数，不动结构。反馈永远改不了问句、改不了五个价值方向。
2. 单步有上限，且权重之和恒为 1（见 judgment/params.py）。
3. 提交前回放历史：新参数如果把过去大多数判断都推翻，就不提交——
   一次情绪化的反馈不该重写全部历史。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from judgment.params import (
    MAX_STEP,
    Parameters,
    ParameterStore,
    ParameterVersion,
    apply_steps,
    project,
)

# 信号名是固定词表：用户的自由表达由 judgment/suggest.py 归类到这里，
# 归不进任何一类的，只能是 UNCLASSIFIED，而不是"最接近的那一类"。
SIGNALS = {
    "TOO_SHALLOW": "太水/没营养/都是空话",
    "TOO_NARROW": "太窄/总是同类/信息茧房",
    "OFF_TOPIC": "跑题/跟我做的事无关",
    "AD_LIKE": "广告/软文/招聘/推销",
    "TOO_STRICT": "漏掉了好的/太保守",
    "TOO_MUCH": "太多了/看不过来",
}

# 每个信号要把参数往哪边推。数值是"推力"，不是"位移"；
# 位移还要经过单步上限与投影（judgment/params.py）。
PULLS: dict[str, dict[str, float]] = {
    "TOO_SHALLOW": {"quality": 1.0, "value": -0.4},
    "TOO_NARROW": {"exploration": 1.0, "direction": -0.4},
    "OFF_TOPIC": {"direction": 1.0, "direction_floor": 0.8},
    "AD_LIKE": {"reduced": 1.0},
    "TOO_STRICT": {"threshold": -1.0},
    "TOO_MUCH": {"threshold": 1.0},
}

MIN_EVENTS = 3
HALF_LIFE_DAYS = 30.0
MAX_WEIGHT_PER_EVENT = 0.5
MAX_FLIP_RATIO = 0.4
MIN_LEDGER_FOR_REPLAY = 20


@dataclass(frozen=True)
class FeedbackEvent:
    event_id: str
    at: str
    kind: str  # ITEM_VETO | ITEM_PROMOTE | STATEMENT
    signal: str | None = None
    item_id: str | None = None
    statement: str | None = None
    summary: dict[str, Any] | None = None
    delivered: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "at": self.at,
            "kind": self.kind,
            "signal": self.signal,
            "item_id": self.item_id,
            "statement": self.statement,
            "summary": self.summary,
            "delivered": self.delivered,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeedbackEvent":
        return cls(
            event_id=str(data["event_id"]),
            at=str(data["at"]),
            kind=str(data["kind"]),
            signal=data.get("signal"),
            item_id=data.get("item_id"),
            statement=data.get("statement"),
            summary=data.get("summary"),
            delivered=data.get("delivered"),
        )


@dataclass
class Proposal:
    accepted: bool
    reason: str
    before: Parameters
    after: Parameters
    moved: dict[str, float] = field(default_factory=dict)
    pulls: dict[str, float] = field(default_factory=dict)
    events: list[FeedbackEvent] = field(default_factory=list)
    guard: dict[str, Any] = field(default_factory=dict)
    needs_agent: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "moved": self.moved,
            "pulls": {key: round(value, 4) for key, value in self.pulls.items()},
            "events": [event.as_dict() for event in self.events],
            "guard": self.guard,
            "needs_agent": self.needs_agent,
        }


def _age_days(event: FeedbackEvent, now: datetime) -> float:
    try:
        stamp = datetime.fromisoformat(event.at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (now - stamp).total_seconds() / 86400.0)


def event_weight(event: FeedbackEvent, now: datetime) -> float:
    """越近的反馈越算数：半衰期 30 天。用户的口味会变，旧证据不该永久生效。"""
    return MAX_WEIGHT_PER_EVENT * (0.5 ** (_age_days(event, now) / HALF_LIFE_DAYS))


FEATURE_FIELDS = ("direction", "value", "quality", "exploration")


def feature_vector(summary: dict[str, Any]) -> dict[str, float]:
    """一条内容在"参数能影响到的四个维度"上的特征。"""
    return {
        "direction": float(summary.get("direction_max", 0.0)),
        "value": float(summary.get("policy_max", summary.get("value_max", 0.0))),
        "quality": float(summary.get("quality", 0.0)),
        "exploration": float(summary.get("exploration", 0.0)),
    }


def estimate_score(parameters: Parameters, summary: dict[str, Any]) -> float:
    features = feature_vector(summary)
    total = 0.0
    for name in FEATURE_FIELDS:
        total += getattr(parameters, name) * features[name]
    if summary.get("reduced"):
        total -= parameters.reduced
    return total


def aggregate_pulls(
    events: list[FeedbackEvent], parameters: Parameters, *, now: datetime
) -> dict[str, float]:
    """把一批反馈翻译成"每个参数往哪边推多少"。

    条目反馈不是一个一个看的，而是一起看的：用户说"要这条、不要那条"，
    真正的信息是**这两条的差别**。所以权重按对比梯度累积——
    被要的条目加它的特征、被弃的条目减它的特征，然后再归一化。

    只按"最大贡献项"降权是个错的做法：它只会把所有分数一起压低，
    排序不变，于是参数动了、结果一模一样。
    """
    pulls: dict[str, float] = {}
    gradient = {name: 0.0 for name in FEATURE_FIELDS}
    promoted: list[dict[str, Any]] = []
    reduced_seen = False

    for event in events:
        weight = event_weight(event, now)
        if event.kind == "STATEMENT":
            for name, pull in PULLS.get(event.signal or "", {}).items():
                pulls[name] = pulls.get(name, 0.0) + pull * (weight / MAX_WEIGHT_PER_EVENT)
            continue

        summary = event.summary or {}
        if not summary:
            continue
        features = feature_vector(summary)
        if event.kind == "ITEM_VETO":
            for name in FEATURE_FIELDS:
                gradient[name] -= weight * features[name]
            reduced_seen = reduced_seen or bool(summary.get("reduced"))
        elif event.kind == "ITEM_PROMOTE":
            for name in FEATURE_FIELDS:
                gradient[name] += weight * features[name]
            promoted.append(summary)

    scale = max((abs(value) for value in gradient.values()), default=0.0)
    if scale > 1e-9:
        for name in FEATURE_FIELDS:
            pulls[name] = gradient[name] / scale

    if reduced_seen:
        # 已经被降权还给用户看到的条目被否决：降权不够重
        pulls["reduced"] = max(pulls.get("reduced", 0.0), 0.6)

    # 用户点名要了某条却没被选中：说明阈值比"他想要的东西"还高
    if promoted:
        strictest = min(estimate_score(parameters, summary) for summary in promoted)
        if strictest < parameters.threshold:
            pulls["threshold"] = -1.0

    # 用户要的东西是被方向门槛直接判 0 的：再怎么调权重也到不了他面前。
    # 这类反馈只能靠下调门槛来兑现，否则"我要的你都过滤掉了"会一直重复发生。
    gated_promotions = [
        summary
        for summary in promoted
        if float(summary.get("direction_max", 0.0)) < parameters.direction_floor
        and float(summary.get("exploration", 0.0)) < 0.5
        and float(summary.get("score", 1.0)) == 0.0
    ]
    if gated_promotions:
        pulls["direction_floor"] = min(pulls.get("direction_floor", 0.0), -1.0)

    return pulls


def learning_rate(commits: int) -> float:
    """每提交一次，步子就小一点：1/sqrt(1+n)。

    固定步长会在最优点两侧来回跳——"这轮改好一点、下轮改坏一点"，
    用户看到的就是推荐忽好忽坏。步长衰减是让这套机制收敛的最简单办法。
    """
    return 1.0 / (1.0 + commits) ** 0.5


def _desired(parameters: Parameters, pulls: dict[str, float], *, rate: float = 1.0) -> dict[str, float]:
    current = parameters.as_dict()
    desired = dict(current)
    for name, pull in pulls.items():
        if name not in MAX_STEP:
            continue
        desired[name] = current[name] + MAX_STEP[name] * rate * max(-1.0, min(1.0, pull))
    return desired


def replay_guard(
    before: Parameters, after: Parameters, ledger: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """把新参数回放到历史上，看会推翻多少已有判断。

    只统计"当时被选中/没被选中"这个二值决定。分数本身变了不重要，
    重要的是用户已经看过的东西会不会被另一套标准重新判一遍。
    """
    rows = list(ledger)
    if len(rows) < MIN_LEDGER_FOR_REPLAY:
        # 历史太短时"会不会推翻历史"没有统计意义，明确标记为未执行，而不是给个假的 ok
        return {"replayed": len(rows), "flipped": 0, "ratio": 0.0, "ok": None, "skipped": "ledger too small"}

    def score(row: dict[str, Any], parameters: Parameters) -> float:
        return (
            parameters.direction * float(row.get("direction_max", 0.0))
            + parameters.value * float(row.get("policy_max", 0.0))
            + parameters.quality * float(row.get("quality", 0.0))
            + parameters.exploration * float(row.get("exploration", 0.0))
            - (parameters.reduced if row.get("reduced") else 0.0)
        )

    def count_flips(parameters: Parameters) -> int:
        flipped = 0
        for row in rows:
            verdict = row.get("verdict")
            if verdict not in {"LIKED", "DISLIKED"}:
                # 没表过态的历史不算数：用户没说过"这条好"，就不该被用来限制参数
                verdict = "LIKED" if row.get("delivered") is True else None
            if verdict is None:
                continue
            would_deliver = score(row, parameters) >= parameters.threshold
            if would_deliver == bool(row.get("delivered")):
                continue  # 决定没变，就不构成"推翻"
            if verdict == "LIKED" and not would_deliver:
                flipped += 1
            elif verdict == "DISLIKED" and would_deliver:
                flipped += 1
        return flipped

    before_flips = count_flips(before)
    after_flips = count_flips(after)
    ratio = after_flips / len(rows)
    return {
        "replayed": len(rows),
        "before_flips": before_flips,
        "after_flips": after_flips,
        "ratio": round(ratio, 4),
        "limit": MAX_FLIP_RATIO,
        # 两个条件都要过：不能比现在更违背用户明说过的偏好（防止越调越差），
        # 也不能一次推翻太多历史（防止一次情绪化反馈重写全部判断）。
        "ok": after_flips <= before_flips and ratio <= MAX_FLIP_RATIO,
    }


def propose(
    store: ParameterStore,
    events: list[FeedbackEvent],
    *,
    ledger: Iterable[dict[str, Any]] = (),
    now: datetime | None = None,
) -> Proposal:
    now = now or datetime.now(timezone.utc)
    current = store.current()
    parameters = current.parameters

    if len(events) < MIN_EVENTS:
        return Proposal(
            accepted=False,
            reason=f"反馈不足（{len(events)}/{MIN_EVENTS}）：一次点击不足以改参数",
            before=parameters,
            after=parameters,
            events=list(events),
        )

    unclassified = [event for event in events if event.kind == "STATEMENT" and not event.signal]
    pulls = aggregate_pulls(events, parameters, now=now)

    if not pulls:
        return Proposal(
            accepted=False,
            reason="这批反馈没有可归因的调整方向",
            before=parameters,
            after=parameters,
            events=list(events),
            needs_agent=bool(unclassified),
        )

    rate = learning_rate(current.version)
    desired = _desired(parameters, pulls, rate=rate)
    after, _ = apply_steps(parameters, desired)
    # 位移必须按"投影之后"算：权重归一化会把某一维推回另一个方向，
    # 报告一个没发生过的变化，比不报告更糟。
    moved = {
        name: round(getattr(after, name) - getattr(parameters, name), 6)
        for name in ("direction", "value", "quality", "exploration", "reduced", "direction_floor", "threshold")
    }
    if not any(abs(value) > 1e-9 for value in moved.values()):
        return Proposal(
            accepted=False,
            reason="已在边界上，没有可移动的空间",
            before=parameters,
            after=after,
            moved=moved,
            pulls=pulls,
            events=list(events),
        )

    guard = replay_guard(parameters, after, ledger)
    if guard.get("ok") is False:
        return Proposal(
            accepted=False,
            reason=(
                f"回放护栏拦截：新参数与已表态偏好的冲突 {guard['before_flips']} → {guard['after_flips']} "
                f"（{guard['replayed']} 条历史，上限 {MAX_FLIP_RATIO:.0%}）"
            ),
            before=parameters,
            after=after,
            moved=moved,
            pulls=pulls,
            events=list(events),
            guard=guard,
        )

    return Proposal(
        accepted=True,
        reason=f"根据 {len(events)} 条反馈调整 {len(moved)} 个参数",
        before=parameters,
        after=after,
        moved=moved,
        pulls=pulls,
        events=list(events),
        guard=guard,
        needs_agent=bool(unclassified),
    )


def commit(
    store: ParameterStore,
    proposal: Proposal,
    *,
    now: datetime | None = None,
    note: str = "",
) -> ParameterVersion:
    """把提案落成一个新版本，并把这批事件标记为已应用。"""
    now = now or datetime.now(timezone.utc)
    current = store.current()
    if current.version == 0 and not (store.versions_dir / "v0000.json").exists():
        # 把"起点"本身也写成版本：否则回滚到先验时无据可依
        store.write(
            ParameterVersion(
                version=0,
                parameters=current.parameters,
                updated_at=current.updated_at or now.isoformat(timespec="seconds"),
                source=current.source,
                events_applied=current.events_applied,
                note=current.note or "初始先验",
            )
        )
    version = store.next_version(current)
    record = ParameterVersion(
        version=version,
        parameters=project(proposal.after.as_dict()),
        updated_at=now.isoformat(timespec="seconds"),
        source="ADAPTED",
        events_applied=current.events_applied + len(proposal.events),
        note=note or proposal.reason,
        replay=proposal.guard,
    )
    store.write(record)
    _append_events(store, proposal.events, record.version)
    clear_pending(store)
    return record


def rollback(store: ParameterStore, version: int, *, now: datetime | None = None, note: str = "") -> ParameterVersion:
    """回滚不是删历史：把旧版本重新提交为一个更新的版本。"""
    now = now or datetime.now(timezone.utc)
    target_path = store.versions_dir / f"v{version:04d}.json"
    if not target_path.exists():
        raise FileNotFoundError(f"没有版本 v{version:04d}")
    target = json.loads(target_path.read_text(encoding="utf-8"))
    current = store.current()
    record = ParameterVersion(
        version=store.next_version(current),
        parameters=project(target["parameters"]),
        updated_at=now.isoformat(timespec="seconds"),
        source="ROLLBACK",
        events_applied=current.events_applied,
        note=note or f"回滚到 v{version:04d}（{target.get('digest', '')}）",
        replay={},
    )
    store.write(record)
    return record


def pending_path(store: ParameterStore) -> Path:
    return store.root / "pending.jsonl"


def append_pending(store: ParameterStore, events: Iterable[FeedbackEvent]) -> int:
    """未满 MIN_EVENTS 的反馈先进队列，够数了才一起提交。

    直接复用"已应用的事件"来凑数会越攒越多：上一轮用过的证据
    下一轮又用一次，参数就会被同一句话反复推动。
    """
    path = pending_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.as_dict(), ensure_ascii=False) + chr(10))
            count += 1
    return count


def load_pending(store: ParameterStore) -> list[FeedbackEvent]:
    path = pending_path(store)
    if not path.exists():
        return []
    return [
        FeedbackEvent.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def clear_pending(store: ParameterStore) -> None:
    path = pending_path(store)
    if path.exists():
        path.unlink()


def _append_events(store: ParameterStore, events: list[FeedbackEvent], version: int) -> None:
    path = store.root / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps({**event.as_dict(), "applied_version": version}, ensure_ascii=False) + chr(10))


def load_events(store: ParameterStore) -> list[FeedbackEvent]:
    path = store.root / "events.jsonl"
    if not path.exists():
        return []
    return [FeedbackEvent.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def record_ledger(store: ParameterStore, rows: Iterable[dict[str, Any]]) -> int:
    """记录"用户看过什么、当时怎么判的"，供回放护栏使用。"""
    path = store.root / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + chr(10))
            count += 1
    return count


def load_ledger(store: ParameterStore, limit: int = 500) -> list[dict[str, Any]]:
    path = store.root / "ledger.jsonl"
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines[-limit:]]
