"""候补池的命令行：把落选的收进去，第二天再拿出来比一次。

    # 收：把这次「过线但没选中」的放进候补
    python tools/backlog.py --carry --items runs/crawl-<日期> --scores <打分行> \
        --selection runs/agent/<日期>/selection.json --ledger store/backlog.jsonl

    # 并：把候补和今天的新候选合到一起，交给终选
    python tools/backlog.py --merge --items runs/crawl-<日期> --scores <打分行> \
        --ledger store/backlog.jsonl --out-items runs/merged/items.json --out-scores runs/merged/scores.jsonl
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

from store.backlog import DEFAULT_BACKLOG, DEFAULT_MAX_ITEMS, DEFAULT_TTL_DAYS  # noqa: E402
from store.backlog import carry, load, merge, save  # noqa: E402
from judgment.params import ParameterStore  # noqa: E402


def _items(path: Path) -> list[dict[str, Any]]:
    target = path / "items.json" if path.is_dir() else path
    if not target.exists():
        print("错误：找不到 {}".format(target), file=sys.stderr)
        raise SystemExit(2)
    return json.loads(target.read_text(encoding="utf-8-sig"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print("错误：找不到 {}".format(path), file=sys.stderr)
        raise SystemExit(2)
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _selected(path: Path | None) -> set[str]:
    if not path:
        return set()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    selection = data.get("selection") or data
    ids = set()
    for key in ("core", "exploration"):
        for row in selection.get(key) or []:
            if row.get("item_id"):
                ids.add(row["item_id"])
    return ids


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="候补池：落选的明天再来")
    parser.add_argument("--items", required=True)
    parser.add_argument("--scores", required=True)
    parser.add_argument("--ledger", default=DEFAULT_BACKLOG)
    parser.add_argument("--selection", default=None, help="--carry 时用来排除已选中的条目")
    parser.add_argument("--threshold", type=float, default=None, help="过线阈值；默认取当前参数版本里的 threshold")
    parser.add_argument("--params", default=str(ROOT / "state" / "params"), help="参数版本库目录")
    parser.add_argument("--user", default="me", help="参数版本库里的用户目录")
    parser.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--now", default=None)
    parser.add_argument("--carry", action="store_true")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--out-items", default=None)
    parser.add_argument("--out-scores", default=None)
    parser.add_argument("--embed", action="store_true", help="用跨语言向量判定「同一件事」（需要本地模型）")
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    items = _items(Path(args.items))
    scores = _jsonl(Path(args.scores))
    existing = load(args.ledger)
    # 候补的门槛必须和候选池是同一条线：docs/32 规则 1 说的是"过线但没选中"。
    # 两条线各写各的，等于让候补收一批从来没资格进候选池的东西。
    current = ParameterStore(args.params, args.user).current()
    threshold = args.threshold if args.threshold is not None else current.parameters.threshold

    if args.carry:
        # 终选分和粗分是两回事：这里用的是打分行的 score 字段，
        # 没有就现场算一次（不跑模型，纯确定性）。
        if any("score" not in row for row in scores):
            from judgment.scoring import summarise

            weights = current.parameters.to_weights()
            for row in scores:
                if "score" not in row and isinstance(row.get("scores"), dict):
                    row["score"] = summarise(row["scores"], weights=weights)["score"]
        result = carry(
            existing,
            items=items,
            scores=scores,
            selected=_selected(Path(args.selection) if args.selection else None),
            threshold=threshold,
            now=now,
            ttl_days=args.ttl_days,
            max_items=args.max_items,
        )
        total = save(args.ledger, result["rows"])
        print(
            "候补：新增 {} 条，合计 {} 条（阈值 {:.3f} / 参数 v{:04d}） → {}".format(
                result["added"], total, threshold, current.version, args.ledger
            )
        )
        return 0

    if args.merge:
        # 跨语言合并：两天前英文进过候补、今天来的是中文那条时，item_id 认不出来。
        same_event = None
        if args.embed:
            from judgment.embeddings import available, cross_lingual_match, embed_items

            if available():
                pool = items + [row["item"] for row in existing if row.get("item")]
                vectors = embed_items(pool)

                def same_event(left, right):  # noqa: F811
                    matched, similarity = cross_lingual_match(
                        vectors.get(left["item_id"], []),
                        left.get("detected_language") or left.get("declared_language"),
                        vectors.get(right["item_id"], []),
                        right.get("detected_language") or right.get("declared_language"),
                    )
                    return {"same": matched, "similarity": similarity}
            else:
                print("没有本地多语言模型，跳过跨语言合并")
        result = merge(
            items, scores, existing, now=now, ttl_days=args.ttl_days, same_event=same_event
        )
        print("合并：今天 {} 条 + 候补回来 {} 条 = {} 条".format(
            len(items), len(result["revived"]), len(result["items"])
        ))
        if result["revived"]:
            print("  回来的：", ", ".join(result["revived"][:8]))
        for row in result.get("merged_events") or []:
            print("  跨语言合并：保留 {}（今天）← 候补 {}，余弦 {}".format(
                row["kept"], row["merged"], row["similarity"]
            ))
        if args.out_items:
            target = Path(args.out_items)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result["items"], ensure_ascii=False, indent=2), encoding="utf-8")
            print("  写出", target)
        if args.out_scores:
            target = Path(args.out_scores)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in result["scores"]),
                encoding="utf-8",
            )
            print("  写出", target)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
