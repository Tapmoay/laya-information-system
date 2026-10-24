"""在固定评测集上跑模型，产出可审计的评测报告。

    python tools/eval_model.py --dataset models/evals/relevance-v1.json --profile MODEL-PROFILE-QWEN25-15B

报告是评测结论的唯一依据：没有报告，模型档案不能从 CANDIDATE 变成 ACTIVE。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from capabilities.adapters.models.invoke import build_request, invoke_decision  # noqa: E402
from capabilities.adapters.models.registry import get_profile, load_profiles  # noqa: E402
from capabilities.adapters.models.resolve import resolve_adapter  # noqa: E402

LABELS = ["DIRECT", "ADJACENT", "EXPLORATORY", "UNRELATED", "INSUFFICIENT_EVIDENCE"]


def run_dataset(dataset: dict, profile: dict, strategy: str | None = None) -> dict:
    adapter = resolve_adapter(profile, strategy=strategy)
    instruction = dataset["demand_context"]
    rows = []
    for item in dataset["items"]:
        request = build_request(
            request_id=f"eval-{item['id']}",
            capability_id=dataset["capability_id"],
            candidate_id=item["id"],
            candidate_text=item["text"],
            options=LABELS,
            instruction=instruction,
            created_at=datetime.now(timezone.utc),
            source_language=item["language"],
            judgment_language="en",
        )
        start = time.time()
        result = invoke_decision(adapter, request, profile, allow_unvalidated=True)
        rows.append(
            {
                "id": item["id"],
                "language": item["language"],
                "expected": item["expected"],
                "actual": result.get("label") if result["status"] == "OK" else result["status"],
                "status": result["status"],
                "seconds": round(time.time() - start, 3),
            }
        )

    correct = [row for row in rows if row["actual"] == row["expected"]]
    by_language: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_language.setdefault(row["language"], {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += int(row["actual"] == row["expected"])

    confusion: dict[str, dict[str, int]] = {}
    for row in rows:
        confusion.setdefault(row["expected"], {}).setdefault(row["actual"], 0)
        confusion[row["expected"]][row["actual"]] += 1

    return {
        "dataset_id": dataset["dataset_id"],
        "model_profile_id": profile["model_profile_id"],
        "model_revision": profile["revision"],
        "adapter_version": profile["adapter_version"],
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(rows),
        "correct": len(correct),
        "accuracy": round(len(correct) / len(rows), 4) if rows else 0.0,
        "by_language": {
            language: {
                "total": bucket["total"],
                "correct": bucket["correct"],
                "accuracy": round(bucket["correct"] / bucket["total"], 4),
            }
            for language, bucket in sorted(by_language.items())
        },
        "confusion": confusion,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="模型评测")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--strategy", default=None, choices=["fewshot", "disambig", "tree", "rationale"]
    )
    args = parser.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    profile = get_profile(load_profiles(), args.profile)

    report = run_dataset(dataset, profile, args.strategy)
    report["strategy"] = args.strategy or "default"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else (
        ROOT / "models" / "evals" / "reports" / f"{dataset['dataset_id']}-{args.profile}-{stamp}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"数据集 {report['dataset_id']}  模型 {report['model_profile_id']}@{report['model_revision']}")
    print(f"总体准确率 {report['correct']}/{report['total']} = {report['accuracy']:.1%}")
    for language, bucket in report["by_language"].items():
        print(f"  {language}: {bucket['correct']}/{bucket['total']} = {bucket['accuracy']:.1%}")
    print("混淆（期望 -> 实际）:")
    for expected, counts in report["confusion"].items():
        detail = ", ".join(f"{actual}:{count}" for actual, count in counts.items())
        print(f"  {expected:22} {detail}")
    print(f"报告：{out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
