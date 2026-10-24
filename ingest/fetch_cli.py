"""单 URL 抓取入口，用于人工核对与调试。

    python -m ingest.fetch_cli --url https://example.com/post --config config/sources.example.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from ingest.fetch import FetchPolicy, fetch


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="受控抓取单个 URL")
    parser.add_argument("--url", required=True)
    parser.add_argument("--config", default="config/sources.example.yaml")
    parser.add_argument("--request-id", default="req-fetch-manual-0001")
    parser.add_argument("--out", default=None, help="把 FetchResult 写到这个文件")
    args = parser.parse_args(argv)

    data = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    policy = FetchPolicy.from_mapping(data)
    result = fetch(args.url, request_id=args.request_id, policy=policy)

    print(f"outcome={result['outcome']} status={result['status_code']} title={result['title']!r}")
    if result["error"]:
        print(f"error={result['error']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["outcome"] != "ERROR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
