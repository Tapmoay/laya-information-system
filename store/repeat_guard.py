"""已推送台账：防止同一条东西第二次出现在你面前。

为什么需要它：每次运行都重新抓一遍列表页，取最新的 N 条。昨天给过的条目今天还在列表上，
没有东西记着"这条我给过了"。实测同一天跑两次，86 条完全相同。

窗口规则（用户 2026-10-19 定的）：
  1. **7 天内一律不推**。重复出现算噪声，不算提醒。
  2. **7 天以后也不自动放行**。只有在「分数明显更高」且「出现了上次没有的关键锚点」时
     才重推——后者就是"有新进展"的可操作定义（新版本号、新日期、新数字）。
  3. 两条都不满足就一直压着。这条是为了堵住"放了 7 天之后又把没价值的东西推一次"。

"同一件事"的判定按 item_id / canonical_url / event_key 三选一命中，
其中 event_key 见 judgment/events.py：同一事件被不同媒体改写标题也能对上。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_LEDGER = "store/delivered.jsonl"
DEFAULT_WINDOW_DAYS = 7
DEFAULT_MARGIN = 0.10


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
            continue  # 坏行跳过，但不能让整个台账失效
    return rows


def remember(path: str | Path, entries: Iterable[dict[str, Any]]) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + chr(10))
            written += 1
    return written


def _parse(moment: Any) -> datetime | None:
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _matches(item: dict[str, Any], prior: dict[str, Any]) -> str | None:
    if item.get("item_id") and item.get("item_id") == prior.get("item_id"):
        return "SAME_ITEM"
    if item.get("canonical_url") and item.get("canonical_url") == prior.get("canonical_url"):
        return "SAME_URL"
    if item.get("event_key") and item.get("event_anchors") and item.get("event_key") == prior.get("event_key"):
        return "SAME_EVENT"
    return None


def check(
    item: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    margin: float = DEFAULT_MARGIN,
) -> dict[str, Any]:
    """返回 {decision, reason, prior, age_days, new_anchors}。

    decision: PUSH（放行）| BLOCK（压住）
    """
    prior: dict[str, Any] | None = None
    how: str | None = None
    for entry in history:
        hit = _matches(item, entry)
        if hit:
            prior, how = entry, hit
            break
    if prior is None:
        return {"decision": "PUSH", "reason": "NEW", "prior": None, "age_days": None, "new_anchors": []}

    delivered_at = _parse(prior.get("delivered_at"))
    age = None
    if delivered_at is not None:
        age = round((now - delivered_at).total_seconds() / 86400.0, 2)
    if age is None or age < window_days:
        return {
            "decision": "BLOCK",
            "reason": "REPEAT_WINDOW",
            "prior": prior,
            "age_days": age,
            "new_anchors": [],
            "match": how,
        }

    anchors = [str(token) for token in (item.get("event_anchors") or [])]
    previous = {str(token) for token in (prior.get("event_anchors") or [])}
    fresh = [token for token in anchors if token and token not in previous]
    score = float(item.get("score") or 0.0)
    before = float(prior.get("score") or 0.0)
    improved = (score - before) >= margin

    if improved and fresh:
        return {
            "decision": "PUSH",
            "reason": "REPUSH_UPDATED",
            "prior": prior,
            "age_days": age,
            "new_anchors": fresh,
            "score_delta": round(score - before, 4),
            "match": how,
        }
    return {
        "decision": "BLOCK",
        "reason": "NO_PROGRESS",
        "prior": prior,
        "age_days": age,
        "new_anchors": fresh,
        "score_delta": round(score - before, 4),
        "match": how,
    }


def entry_for(item: dict[str, Any], *, now: datetime, channel: str = "final") -> dict[str, Any]:
    return {
        "item_id": item.get("item_id"),
        "canonical_url": item.get("canonical_url"),
        "event_key": item.get("event_key"),
        "event_cluster_id": item.get("event_cluster_id"),
        "event_anchors": item.get("event_anchors") or [],
        "score": item.get("score"),
        "title": item.get("title"),
        "delivered_at": now.isoformat(timespec="seconds"),
        "channel": channel,
    }


def partition(
    items: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    margin: float = DEFAULT_MARGIN,
) -> dict[str, Any]:
    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in items:
        verdict = check(item, history, now=now, window_days=window_days, margin=margin)
        if verdict["decision"] == "PUSH":
            allowed.append(item)
        else:
            blocked.append({**verdict, "item_id": item.get("item_id"), "title": item.get("title")})
    return {"allowed": allowed, "blocked": blocked}
