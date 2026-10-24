"""落选候补：这次没选上的，明天再来一次。

为什么需要：抓取只看列表页第一屏。一条今天上了列表、今天没被选中的内容，
明天就不在列表上了——**它再也不会被看见**。这不是"重要信息被筛掉"，
是"重要信息根本没机会再被考虑"。

规则：

1. 只有**过了分数门槛但没被选中**的条目才进候补。连门槛都没到的，是判断它不值得，
   不是没轮到它，不该反复回来占位置。
2. 候补有寿命（默认 7 天）。过期的直接丢——放太久的东西，时效性本身就不成立了。
3. 候补条目**保留上次的测量结果**，第二天不重跑模型。内容没变，重跑只是烧 GPU。
4. 上限（默认 80 条），超出时按上次分数留高的。
5. 被选中过的不再进候补；这一条由调用方传入选中集合。

和"已推送台账"的分工：
- 台账管**已经给过你的**，防止重复推送。
- 候补管**差一点给上的**，防止漏掉。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

DEFAULT_BACKLOG = "store/backlog.jsonl"
DEFAULT_TTL_DAYS = 7
DEFAULT_MAX_ITEMS = 80


def _parse(moment: Any) -> datetime | None:
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def save(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    materialized = list(rows)
    target.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in materialized), encoding="utf-8"
    )
    return len(materialized)


def prune(rows: list[dict[str, Any]], *, now: datetime, ttl_days: int = DEFAULT_TTL_DAYS) -> list[dict[str, Any]]:
    kept = []
    for row in rows:
        carried = _parse(row.get("carried_at"))
        if carried is None:
            continue
        if now - carried > timedelta(days=ttl_days):
            continue
        kept.append(row)
    return kept


def merge(
    today_items: list[dict[str, Any]],
    today_scores: list[dict[str, Any]],
    backlog: list[dict[str, Any]],
    *,
    now: datetime,
    ttl_days: int = DEFAULT_TTL_DAYS,
    same_event: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把今天的候选和候补合到一起。

    合并两种"同一条"：

    1. 同一个 item_id —— 今天又抓到了同一条，以今天为准。
    2. **同一个事件、不同语言** —— 两天前用英文进过候补，今天来的是中文那条。
       这种情况靠 item_id 认不出来（id 不同），只能靠 same_event 回调
       （默认接跨语言向量判定）。认出来之后**只留今天那条**：
       同一件事占两个候选位是浪费，而且今天那条通常更新、更好读。
       候补那条的等待次数会带到今天这条上，不会白等。
    """
    alive = prune(backlog, now=now, ttl_days=ttl_days)
    seen = {item["item_id"] for item in today_items}
    items = list(today_items)
    scores = list(today_scores)
    revived: list[str] = []
    merged_events: list[dict[str, Any]] = []

    for row in alive:
        item = row.get("item") or {}
        item_id = item.get("item_id")
        if not item_id or item_id in seen:
            continue

        parent = None
        if same_event is not None:
            for candidate in items:
                verdict = same_event(item, candidate)
                if verdict.get("same"):
                    parent = (candidate, verdict)
                    break
        if parent is not None:
            target, verdict = parent
            target["backlog_merged_from"] = (target.get("backlog_merged_from") or []) + [item_id]
            target["backlog_attempts"] = int(target.get("backlog_attempts", 0)) + int(row.get("attempts", 1)) + 1
            target.setdefault("backlog_merge_similarity", verdict.get("similarity"))
            merged_events.append(
                {"kept": target["item_id"], "merged": item_id, "similarity": verdict.get("similarity")}
            )
            seen.add(item_id)
            continue

        seen.add(item_id)
        items.append({**item, "from_backlog": True, "backlog_attempts": row.get("attempts", 1)})
        score_row = row.get("scores")
        if score_row:
            scores.append({**score_row, "from_backlog": True})
        revived.append(item_id)

    return {
        "items": items,
        "scores": scores,
        "revived": revived,
        "merged_events": merged_events,
        "expired": [r.get("item", {}).get("item_id") for r in backlog if r not in alive],
    }


def carry(
    backlog: list[dict[str, Any]],
    *,
    items: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    selected: set[str],
    threshold: float,
    now: datetime,
    ttl_days: int = DEFAULT_TTL_DAYS,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> dict[str, Any]:
    """把「过线但没选中」的条目放进候补；已经在候补里的累加次数。"""
    item_by_id = {item["item_id"]: item for item in items}
    score_by_id = {row["item_id"]: row for row in scores}
    alive = {row["item"]["item_id"]: row for row in prune(backlog, now=now, ttl_days=ttl_days)}

    added = 0
    for item_id, item in item_by_id.items():
        if item_id in selected:
            alive.pop(item_id, None)  # 已经给过了，不必再候补
            continue
        row = score_by_id.get(item_id)
        if not row:
            continue
        try:
            value = float(row.get("score"))
        except (TypeError, ValueError):
            value = 0.0
        if value < threshold:
            alive.pop(item_id, None) if item_id in alive else None
            continue
        if item_id in alive:
            alive[item_id]["attempts"] = int(alive[item_id].get("attempts", 1)) + 1
            alive[item_id]["scores"] = row
        else:
            alive[item_id] = {
                "item": item,
                "scores": row,
                "carried_at": now.isoformat(timespec="seconds"),
                "attempts": 1,
            }
            added += 1

    ordered = sorted(
        alive.values(),
        key=lambda row: -float((row.get("scores") or {}).get("score") or 0.0),
    )[:max_items]
    return {"rows": ordered, "added": added, "total": len(ordered)}
