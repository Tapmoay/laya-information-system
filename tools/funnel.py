"""两级筛选的命令行：粗选（已有分数）→ 摘要复判（跑模型）→ 候选池。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.funnel import coarse, finalize, refine  # noqa: E402
from judgment.params import PRIOR, Parameters  # noqa: E402
from judgment.questions import build_questions, to_engine_questions  # noqa: E402


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="两级筛选：粗选 → 摘要复判")
    parser.add_argument("--scores", required=True, help="粗选结果（score_items 的输出）")
    parser.add_argument("--items", required=True, help="crawl 的 items.json（提供正文）")
    parser.add_argument("--demand", required=True)
    parser.add_argument("--out", default="runs/funnel/funnel.json")
    parser.add_argument("--limit", type=int, default=15, help="进入第二级的条数上限")
    parser.add_argument("--coarse", type=float, default=None, help="粗选阈值，默认取先验阈值")
    parser.add_argument("--refine", type=float, default=None, help="复判阈值，默认同粗选")
    parser.add_argument("--model", default="models/strands-decider-2b-local")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--summary-chars", type=int, default=320)
    args = parser.parse_args(argv)

    rows = _load_jsonl(Path(args.scores))
    bodies = {
        item["item_id"]: item.get("text_clean") or item.get("text") or ""
        for item in json.loads(Path(args.items).read_text(encoding="utf-8-sig"))
    }
    threshold = args.coarse if args.coarse is not None else Parameters(**PRIOR).threshold
    refine_threshold = args.refine if args.refine is not None else threshold

    result = coarse(rows, threshold=threshold, limit=args.limit)
    print("粗选：{} 条 → 过线 {} 条（阈值 {}）".format(len(rows), len(result.passed_coarse), threshold))

    demand = json.loads(Path(args.demand).read_text(encoding="utf-8-sig"))
    specs = build_questions(demand)
    # 第二级去掉四个方向槽：摘要太短，问方向只会得到"判不出来"，然后把分数清零
    refine_specs = [spec for spec in specs if spec.tag in {"policy", "quality", "form"}]
    engine_questions = to_engine_questions(refine_specs)
    sys.path.insert(0, str(ROOT / "models" / "_tf5"))
    from strands_decider.infer import load_engine

    print("第二级问句数：{}（不含方向槽）".format(len(refine_specs)))
    engine = load_engine(args.model, device=args.device)

    def ask(text: str) -> dict[str, Any]:
        answers = engine.ask(text, engine_questions).answers
        scores: dict[str, Any] = {}
        for name, answer in answers.items():
            if hasattr(answer, "noul"):
                scores[name] = {"noul": float(answer.noul)}
            else:
                scores[name] = {"probabilities": {k: float(v) for k, v in dict(answer.probabilities).items()}}
        return scores

    result = refine(result, ask=ask, bodies=bodies, threshold=refine_threshold,
                    summary_chars=args.summary_chars)
    final = finalize(result)
    for item in result.refined:
        refined = "-" if item.refined_score is None else round(item.refined_score, 3)
        print("  {:<9s} 粗={:.3f} 复={} | {} | {}".format(
            item.verdict, item.coarse_score, refined, item.title[:36], item.note))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"funnel": result.as_dict(), "final": final}, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )
    print("写入", out)
    print("候选池：", [item["item_id"] for item in final["core"]])
    print("待复看：", [item["item_id"] for item in final["needs_review"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
