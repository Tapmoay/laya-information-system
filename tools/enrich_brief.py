"""用 Agent 生成的增强内容重渲染简报（CAP-DEL-05）。

    python tools/enrich_brief.py --run runs/real-v2 --enrichment runs/real-v2/enrichment.json

增持内容只进呈现层：不写回测量、不改变选择、不参与推送幂等键。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery import enrichment as enrich_lib  # noqa: E402
from delivery.renderer import render_brief  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="用增强内容重渲染简报")
    parser.add_argument("--run", required=True)
    parser.add_argument("--enrichment", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    run_dir = Path(args.run)
    delivery = json.loads((run_dir / "delivery-object.json").read_text(encoding="utf-8"))
    decisions = json.loads((run_dir / "selection-decisions.json").read_text(encoding="utf-8"))
    measurements = json.loads((run_dir / "measurements.json").read_text(encoding="utf-8"))
    payload = enrich_lib.load(args.enrichment)

    candidate_ids = [item["candidate_id"] for item in delivery["items"]]
    problems = enrich_lib.validate(payload, candidate_ids)
    if problems:
        print("增强内容不完整：")
        for problem in problems:
            print(f"  - {problem}")
        return 2

    index = enrich_lib.index(payload)
    brief = render_brief(
        delivery,
        __import__("datetime").datetime.fromisoformat(payload["generated_at"]),
        decisions=decisions,
        measurements=measurements,
        enrichment=index,
    )
    out_dir = Path(args.out) if args.out else run_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rendered-brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "rendered-brief.md").write_text(brief["body_markdown"] + "\n", encoding="utf-8")
    print(brief["body_markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
