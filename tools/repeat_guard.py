"""已推送台账的命令行：过滤 + 记账。

    python tools/repeat_guard.py --items runs/crawl-<日期> --scores <打分行> \
        --ledger store/delivered.jsonl --out runs/crawl-<日期>/repeat.json

    python tools/repeat_guard.py --record --selection runs/agent/<日期>/selection.json \
        --items runs/crawl-<日期> --ledger store/delivered.jsonl

为什么单独做成一个工具而不是塞进选择器：
它管的是"要不要让你再看见一次"，和"这条值不值得看"是两回事。
混在一起之后，一旦台账出问题，分数也会跟着不可信。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.events import cluster as cluster_by_anchors  # noqa: E402
from judgment.facts import Claim, cluster as cluster_by_facts  # noqa: E402
from judgment.scoring import summarise  # noqa: E402
from store.repeat_guard import DEFAULT_LEDGER, DEFAULT_MARGIN, DEFAULT_WINDOW_DAYS  # noqa: E402
from store.fact_library import load as load_facts, lookup as lookup_facts  # noqa: E402
from store.repeat_guard import entry_for, load, partition, remember  # noqa: E402


def _items(path: Path) -> list[dict[str, Any]]:
    target = path / "items.json" if path.is_dir() else path
    if not target.exists():
        print("错误：找不到 {}".format(target), file=sys.stderr)
        raise SystemExit(2)
    data = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        print("错误：{} 不是条目数组".format(target), file=sys.stderr)
        raise SystemExit(2)
    broken = [item for item in data if not isinstance(item, dict) or not item.get("item_id")]
    if broken:
        print("警告：{} 条缺少 item_id，已跳过".format(len(broken)), file=sys.stderr)
    return [item for item in data if isinstance(item, dict) and item.get("item_id")]


def _scores(path: Path) -> dict[str, float]:
    table: dict[str, float] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        item_id = row.get("item_id")
        raw = row.get("scores")
        if not item_id or not isinstance(raw, dict):
            continue
        table[item_id] = float(summarise(raw)["score"])
    return table


def _facts_path(target: Path) -> Path | None:
    base = target if target.is_dir() else target.parent
    candidate = base / "facts.jsonl"
    return candidate if candidate.exists() else None


def _attach_facts(items: list[dict[str, Any]], path: Path) -> None:
    table: dict[str, list[Claim]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        table[row["item_id"]] = [
            Claim(c["subject"], c["relation"], c["object"], c.get("time", "-"))
            for c in (row.get("claims") or [])
        ]
    for item in items:
        item["claims"] = table.get(item["item_id"], [])


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="已推送台账：过滤重复与记账")
    parser.add_argument("--items", required=True, help="抓取目录或 items.json")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--scores", default=None, help="打分行；给了才能比较「分数是否明显更高」")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    parser.add_argument("--out", default=None, help="过滤结果写到哪里")
    parser.add_argument("--record", action="store_true", help="把这次选中的写进台账")
    parser.add_argument("--selection", default=None)
    parser.add_argument("--now", default=None, help="覆盖当前时间，便于复算")
    parser.add_argument("--facts-lib", default=None, help="事实库；给了就再查一遍有没有同样的说法")
    args = parser.parse_args(argv)

    items = _items(Path(args.items))
    facts_path = _facts_path(Path(args.items))
    if facts_path is not None:
        _attach_facts(items, facts_path)
        mapping = cluster_by_facts(items)
        print("按事实聚簇（来源 {}）".format(facts_path.name))
    else:
        mapping = cluster_by_anchors(items)
        print("没有 facts.jsonl，退回按锚点聚簇（只并跨源）")
    for item in items:
        for key, value in mapping.get(item["item_id"], {}).items():
            item.setdefault(key, value)
    if args.scores:
        table = _scores(Path(args.scores))
        for item in items:
            if item["item_id"] in table:
                item["score"] = table[item["item_id"]]

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)

    if args.record:
        if not args.selection:
            print("--record 需要 --selection", file=sys.stderr)
            return 2
        selection = json.loads(Path(args.selection).read_text(encoding="utf-8-sig"))
        chosen = selection.get("selection") or {}
        ids = {row["item_id"] for key in ("core", "exploration") for row in chosen.get(key) or []}
        entries = [entry_for(item, now=now) for item in items if item["item_id"] in ids]
        print("已记账 {} 条 → {}".format(remember(args.ledger, entries), args.ledger))
        return 0

    result = partition(items, load(args.ledger), now=now, window_days=args.window_days, margin=args.margin)

    # 台账管"这条我推过没有"，事实库管"这件事我说过没有"。
    # 后者更粗但更早生效：事实库里一模一样 → 排除；只相似 → 当更新，放行但打标记。
    if args.facts_lib:
        library = load_facts(args.facts_lib)
        still_allowed = []
        for item in result["allowed"]:
            verdict = lookup_facts(item, library)
            if verdict["verdict"] == "IDENTICAL":
                result["blocked"].append(
                    {
                        "item_id": item.get("item_id"),
                        "title": item.get("title"),
                        "reason": "FACT_IDENTICAL",
                        "ratio": verdict["ratio"],
                    }
                )
                continue
            if verdict["verdict"] == "SIMILAR":
                item["update_of"] = (verdict.get("entry") or {}).get("item_id")
                item["update_ratio"] = verdict["ratio"]
            still_allowed.append(item)
        result["allowed"] = still_allowed

    print("放行 {} 条 · 压住 {} 条".format(len(result["allowed"]), len(result["blocked"])))
    for row in result["blocked"][:20]:
        print("  压住 {} {} age={} {}".format(
            str(row.get("item_id")), str(row.get("reason")), row.get("age_days"), (row.get("title") or "")[:30]
        ))
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
        print("写出", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
