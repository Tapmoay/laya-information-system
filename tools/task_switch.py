"""切换当前任务：任务槽只有一个，换的时候要同时改候选池和需求快照。

    python tools/task_switch.py --list
    python tools/task_switch.py --enable task-system-build

存在的理由：当前任务从"建这个系统"变成"还不确定下一个是什么"之后，
任务槽就不再是写死在快照里的常量。如果每次切换都要手改两个 JSON，
早晚会出现"池子说 A、快照说 B"的分叉，而那一分叉不会报错，
只会让之后所有打分悄悄偏离。

这个工具只做两件事：把候选池里被选中的那一项标成 ENABLED，
再把需求快照的 active_tasks 重写成 [启用项(ACTIVE)] + [历史(DONE/BLOCKED)]。
历史不删——否则"上个月在做什么"就查不出来了。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POOL = ROOT / "demand" / "task-pool.json"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")


def latest_demand() -> Path:
    """按文件名里的日期取最新快照。日期格式固定，字典序就是时间序。"""
    candidates = sorted((ROOT / "demand").glob("demand-2*.json"))
    if not candidates:
        raise SystemExit("找不到 demand/demand-2*.json")
    return candidates[-1]


def enabled(pool: dict[str, Any]) -> dict[str, Any] | None:
    for item in pool.get("candidates") or []:
        if item.get("state") == "ENABLED":
            return item
    return None


def show(pool: dict[str, Any]) -> None:
    current = enabled(pool)
    print("候选池：" + str(pool.get("pool_id")))
    for item in pool.get("candidates") or []:
        mark = "*" if item is current else " "
        print(" " + mark + " " + str(item.get("task_id")).ljust(28) + str(item.get("state")).ljust(11) + str(item.get("statement")))
    if current is None:
        print(chr(10) + "警告：没有任何候选被启用，任务槽会退化成「当前没有明确任务」。")
    else:
        print(chr(10) + "当前任务：" + str(current.get("task_id")))


def switch(task_id: str, demand_path: Path | None = None) -> int:
    pool = _load(POOL)
    target = None
    for item in pool.get("candidates") or []:
        if item.get("task_id") == task_id:
            target = item
    if target is None:
        print("候选池里没有 " + task_id, file=sys.stderr)
        return 2

    now = datetime.now().astimezone().replace(microsecond=0).isoformat()
    previous = enabled(pool)
    for item in pool["candidates"]:
        if item is target:
            item["state"] = "ENABLED"
            item["since"] = now
        elif item.get("state") == "ENABLED":
            item["state"] = "CANDIDATE"
            item.pop("since", None)
    # 被换下去的任务在快照里会消失（快照只保留一个 ACTIVE），所以轮换史记在池子里。
    # 不记的话，"上个月当前任务是哪个"就查不出来了。
    if previous is not None and previous.get("task_id") != task_id:
        pool.setdefault("history", []).append(
            {"from": previous.get("task_id"), "to": task_id, "at": now}
        )
    pool["updated_at"] = now
    _write(POOL, pool)

    path = demand_path or latest_demand()
    demand = _load(path)
    history = [
        item
        for item in (demand.get("active_tasks") or [])
        if item.get("task_id") != task_id and item.get("status") != "ACTIVE"
    ]
    demand["active_tasks"] = [
        {
            "task_id": task_id,
            "statement": target.get("statement"),
            "status": "ACTIVE",
            "source_ref": "demand/task-pool.json#" + task_id,
        }
    ] + history
    demand["created_at"] = now
    _write(path, demand)

    print("已切换当前任务 → " + task_id + "（" + str(path.relative_to(ROOT)) + " 已重写）")
    print("任务槽文本：" + str(target.get("statement")))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="切换当前任务（候选池 + 需求快照）")
    parser.add_argument("--list", action="store_true", help="看候选池当前状态")
    parser.add_argument("--enable", default=None, metavar="TASK_ID", help="启用某个候选")
    parser.add_argument("--demand", default=None, help="指定需求快照，默认取最新")
    args = parser.parse_args(argv)

    pool = _load(POOL)
    if args.enable:
        return switch(args.enable, Path(args.demand) if args.demand else None)
    show(pool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
