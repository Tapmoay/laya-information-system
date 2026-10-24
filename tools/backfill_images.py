"""给已抓取的条目回填配图：从 raw/<item_id>.html 里重新提取。

存在的理由：配图信息一直躺在原始 HTML 里，只是早期版本没有解析它。
回填比重抓便宜得多，也不会因为列表页更新而换掉一批内容。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.images import extract_images  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="回填配图")
    parser.add_argument("--dir", required=True, help="抓取输出目录")
    parser.add_argument("--file", default=None, help="只处理某个 items 文件（默认目录下的 items.json）")
    parser.add_argument("--limit", type=int, default=3, help="每条最多保留几张")
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    items_path = Path(args.file) if args.file else directory / "items.json"
    items = json.loads(items_path.read_text(encoding="utf-8-sig"))
    raw_dir = directory / "raw"

    filled = 0
    for item in items:
        raw_path = raw_dir / (item["item_id"] + ".html")
        if not raw_path.exists():
            continue
        html = raw_path.read_text(encoding="utf-8", errors="replace")
        base_url = (item.get("provenance") or {}).get("final_url") or item.get("canonical_url") or ""
        images = extract_images(html, base_url, limit=args.limit)
        item["images"] = images
        if images:
            filled += 1

    items_path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    print("处理 {} 条，其中 {} 条取到配图".format(len(items), filled))
    print("写入", items_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
