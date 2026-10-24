"""本地演示：起一个静态服务器，跑一遍 M2 在线链路，把产物写到 runs/。

    python -m tools.demo_live_run --out runs/2026-10-18/live-demo
"""

from __future__ import annotations

import argparse
import http.server
import json
import threading
from datetime import datetime
from pathlib import Path

from delivery.push import PushConfig
from ingest.fetch import FetchPolicy
from runtime.live_run import LiveRunConfig, run_live

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = ROOT / "fixtures" / "html"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HTML_DIR), **kwargs)

    def log_message(self, *args):
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地 M2 链路演示")
    parser.add_argument("--out", default="runs/demo-live")
    parser.add_argument("--run-id", default="run-live-demo")
    args = parser.parse_args(argv)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        demand = json.loads((ROOT / "fixtures" / "m1" / "basic" / "demand-snapshot.json").read_text(encoding="utf-8"))
        policy_config = json.loads((ROOT / "fixtures" / "m1" / "basic" / "policy-config.json").read_text(encoding="utf-8"))
        out = Path(args.out)
        config = LiveRunConfig(
            urls=[f"{base}/article.html", f"{base}/chinese.html", f"{base}/missing.html"],
            demand=demand,
            policy_config=policy_config,
            fetch_policy=FetchPolicy(allowed_hosts=("127.0.0.1",), timeout_seconds=5, retry_backoff_seconds=0.01),
            push=PushConfig.from_mapping({"delivery": {"channel": "file", "target": str(out / "outbox")}}),
        )
        result = run_live(
            config,
            run_id=args.run_id,
            out_dir=out,
            now=datetime.fromisoformat("2026-10-18T04:30:00+08:00"),
        )
    finally:
        httpd.shutdown()

    manifest = result["manifest"]
    print(f"run_status={manifest['run_status']} delivery_status={manifest['delivery_status']}")
    print(f"counts={manifest['counts']}")
    for stage in manifest["stages"]:
        print(f"  {stage['stage']:<18} {stage['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
