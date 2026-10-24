"""生成标注页：把没标过的内容做成一页可以点「要/不要」的网页。

python tools/label_page.py --items runs/crawl-20261019c/items.json \
    --demand demand/demand-2026-01-01.json --limit 30 --out runs/label/2026-10-19
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.label_page import render  # noqa: E402
from judgment.text import summarise  # noqa: E402


def _labeled_ids(path: Path | None) -> set[str]:
    if not path or not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            ids.add(json.loads(line)["item_id"])
    return ids


def _pick(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按领域轮流取，保证不是一屏全是设计稿。"""
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_domain.setdefault(item.get("domain") or "unknown", []).append(item)
    picked: list[dict[str, Any]] = []
    index = 0
    while len(picked) < limit:
        added = False
        for domain in sorted(by_domain):
            bucket = by_domain[domain]
            if index < len(bucket) and len(picked) < limit:
                picked.append(bucket[index])
                added = True
        if not added:
            break
        index += 1
    return picked


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="生成标注页")
    parser.add_argument("--items", required=True)
    parser.add_argument("--demand", default=None)
    parser.add_argument("--exclude-labels", default="calibration/labels-user-ai.jsonl",
                        help="已经标过的条目不再出现")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--ids", default=None, help="只取这些 item_id（逗号分隔），按给定顺序")
    parser.add_argument("--translations", default=None,
                        help="翻译文件（tools/translate_items.py 的产物）；英文条目会显示中文译文")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    items_path = Path(args.items)
    if items_path.is_dir():
        items_path = items_path / "items.json"
    items = json.loads(items_path.read_text(encoding="utf-8-sig"))
    seen = _labeled_ids(Path(args.exclude_labels) if args.exclude_labels else None)
    pool = [item for item in items if item["item_id"] not in seen]
    if args.ids:
        wanted = [token.strip() for token in args.ids.split(",") if token.strip()]
        index = {item["item_id"]: item for item in pool}
        picked = [index[item_id] for item_id in wanted if item_id in index][: args.limit]
    else:
        picked = _pick(pool, args.limit)

    summaries = {
        item["item_id"]: summarise(item.get("text_clean") or item.get("text") or "", item.get("title") or "", 320)
        for item in picked
    }
    images = {item["item_id"]: item.get("images") or [] for item in picked}
    demand = json.loads(Path(args.demand).read_text(encoding="utf-8-sig")) if args.demand else None

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    translations = {}
    if args.translations and Path(args.translations).exists():
        translations = json.loads(Path(args.translations).read_text(encoding="utf-8"))
    html = render(picked, summaries=summaries, images=images, demand=demand, translations=translations,
                  generated_at=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"))
    page = out / "label.html"
    page.write_text(html, encoding="utf-8")
    (out / "items.json").write_text(
        json.dumps([{k: v for k, v in item.items() if k != "text"} for item in picked],
                   ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")

    print("池子 {} 条（已排除已标 {} 条），取 {} 条".format(len(pool), len(items) - len(pool), len(picked)))
    print("领域分布:", dict(Counter(item.get("domain") for item in picked)))
    print("页面:", page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
