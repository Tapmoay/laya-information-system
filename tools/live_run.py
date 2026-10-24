"""在线链路命令行：读来源配置 → 抓取 → 测量 → 选择 → 交付。

    python -m tools.live_run --sources config/live_sources.yaml --out runs/real
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.live_config import load_live_config  # noqa: E402
from runtime.live_run import run_live  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="在线运行一轮")
    parser.add_argument("--sources", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now().astimezone()
    run_id = args.run_id or f"run-live-{now:%Y%m%d%H%M}"
    config = load_live_config(args.sources)

    result = run_live(config, run_id=run_id, out_dir=args.out, now=now)
    manifest = result["manifest"]
    print(
        f"run_id={run_id} run_status={manifest.get('run_status')} "
        f"delivery_status={manifest.get('delivery_status')}"
    )
    print(f"counts={manifest.get('counts')}")
    brief = result.get("brief") or {}
    text = (brief.get("body_markdown") or "").strip()
    if text:
        print("-" * 60)
        print(text[:1500])
    if result["failed_stage"]:
        print(f"失败阶段 {result['failed_stage']}：{result['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
