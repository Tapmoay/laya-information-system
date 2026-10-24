"""对照两个判断 provider 在同一批内容上的测量，回答"换模型要不要重调阈值"。

    python tools/compare_judges.py --scores-a runs/live-2026-10-24/scores.jsonl ^
        --scores-b runs/cloud-compare/scores.jsonl --items runs/live-2026-10-24/items.json ^
        --out runs/cloud-compare/compare.md

它只做描述性统计，不下"谁更准"的结论——那需要人工标注集。
能回答的三个问题：

1. 同一批内容，两套分数差多少（逐问句、合成分）；
2. 排序是否一致（Spearman、top-K 重合）；
3. 若要保持同样的入选规模，新 provider 的阈值大致该放在哪（分布对齐，不是正确性证明）。

一个必须写进报告的局限：云端给的是**自报分数**，本地 decider 给的是 logit 头，
两者不是同一种概率。分数接近不等于校准一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.questions import build_questions  # noqa: E402
from judgment.recency import measure as measure_recency  # noqa: E402
from judgment.scoring import DEFAULT_WEIGHTS, summarise  # noqa: E402

THRESHOLDS = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)


def _load_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    return {row["item_id"]: row for row in rows}


def _ranks(values: Sequence[float]) -> list[float]:
    """平均秩，处理并列。"""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[order[position]] = average
        index = end + 1
    return ranks


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return cov / (var_x * var_y) ** 0.5


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    return pearson(_ranks(xs), _ranks(ys))


def _observed_at(item: dict[str, Any]) -> datetime:
    raw = (item.get("provenance") or {}).get("index_fetched_at")
    try:
        observed = datetime.fromisoformat(raw) if raw else datetime.now(timezone.utc)
    except ValueError:
        observed = datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed


def _composite(item: dict[str, Any], row: dict[str, Any]) -> float:
    body = item.get("text_clean") or item.get("text") or ""
    recency = measure_recency(item, _observed_at(item))
    return float(
        summarise(
            row.get("scores") or {},
            weights=DEFAULT_WEIGHTS,
            age_days=recency["age_days"],
            text=body,
            title=item.get("title") or "",
        )["score"]
    )


def _overlap(a: list[str], b: list[str], k: int) -> float:
    if k <= 0:
        return 0.0
    return len(set(a[:k]) & set(b[:k])) / k


def _threshold_for_count(scores: list[float], count: int) -> float | None:
    if not scores or count <= 0:
        return None
    ordered = sorted(scores, reverse=True)
    return round(ordered[min(count, len(ordered)) - 1], 4)


def compare(
    rows_a: dict[str, dict[str, Any]],
    rows_b: dict[str, dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    label_a: str = "local",
    label_b: str = "cloud",
    top: int = 10,
) -> dict[str, Any]:
    item_by_id = {item["item_id"]: item for item in items}
    shared = [item for item in items if item["item_id"] in rows_a and item["item_id"] in rows_b]
    if not shared:
        return {"n": 0}

    question_names = [
        spec.name for spec in build_questions({"active_goals": [], "active_tasks": []}) if spec.kind == "noul"
    ]
    per_question: list[dict[str, Any]] = []
    for name in question_names:
        a = [float((rows_a[item["item_id"]]["scores"].get(name) or {}).get("noul", 0.0)) for item in shared]
        b = [float((rows_b[item["item_id"]]["scores"].get(name) or {}).get("noul", 0.0)) for item in shared]
        deltas = [y - x for x, y in zip(a, b)]
        per_question.append(
            {
                "name": name,
                "mean_a": round(sum(a) / len(a), 4),
                "mean_b": round(sum(b) / len(b), 4),
                "delta": round(sum(deltas) / len(deltas), 4),
                "mean_abs_delta": round(sum(abs(value) for value in deltas) / len(deltas), 4),
                "rho": spearman(a, b),
            }
        )

    form_agreement = 0
    for item in shared:
        probs_a = ((rows_a[item["item_id"]]["scores"].get("form") or {}).get("probabilities") or {})
        probs_b = ((rows_b[item["item_id"]]["scores"].get("form") or {}).get("probabilities") or {})
        top_a = max(probs_a, key=probs_a.get) if probs_a else None
        top_b = max(probs_b, key=probs_b.get) if probs_b else None
        form_agreement += int(top_a == top_b and top_a is not None)

    scores_a = {item["item_id"]: _composite(item, rows_a[item["item_id"]]) for item in shared}
    scores_b = {item["item_id"]: _composite(item, rows_b[item["item_id"]]) for item in shared}
    ordered_a = sorted(scores_a, key=scores_a.get, reverse=True)
    ordered_b = sorted(scores_b, key=scores_b.get, reverse=True)
    values_a = [scores_a[item["item_id"]] for item in shared]
    values_b = [scores_b[item["item_id"]] for item in shared]

    pass_rates = {
        "a": {threshold: sum(1 for value in values_a if value >= threshold) / len(values_a)
              for threshold in THRESHOLDS},
        "b": {threshold: sum(1 for value in values_b if value >= threshold) / len(values_b)
              for threshold in THRESHOLDS},
    }

    return {
        "n": len(shared),
        "label_a": label_a,
        "label_b": label_b,
        "per_question": per_question,
        "form_agreement": form_agreement / len(shared),
        "mean_a": round(sum(values_a) / len(values_a), 4),
        "mean_b": round(sum(values_b) / len(values_b), 4),
        "mean_abs_delta": round(
            sum(abs(scores_b[item["item_id"]] - scores_a[item["item_id"]]) for item in shared)
            / len(shared),
            4,
        ),
        "rho": spearman(values_a, values_b),
        "top": {k: _overlap(ordered_a, ordered_b, k) for k in (5, 10, top) if k > 0},
        "pass_rates": pass_rates,
        "threshold_b_for_a_045": _threshold_for_count(
            values_b, sum(1 for value in values_a if value >= 0.45)
        ),
        "top_ids_a": ordered_a[:top],
        "top_ids_b": ordered_b[:top],
        "scores_a": scores_a,
        "scores_b": scores_b,
    }


def render(report: dict[str, Any], *, scores_a_path: str, scores_b_path: str) -> str:
    if report["n"] == 0:
        return "# 判断 provider 对照\n\n没有共同条目，无法对照。\n"
    rho = "n/a" if report["rho"] is None else f"{report['rho']:.3f}"
    lines = [
        "# 判断 provider 对照",
        "",
        f"- 对照条目：**{report['n']}**",
        f"- A：`{report['label_a']}`（`{scores_a_path}`）",
        f"- B：`{report['label_b']}`（`{scores_b_path}`）",
        f"- 合成分均值：A {report['mean_a']:.3f} / B {report['mean_b']:.3f}，平均绝对差 {report['mean_abs_delta']:.3f}",
        f"- 合成分 Spearman：**{rho}**；形态一致率 {report['form_agreement']:.1%}",
        "",
        "## 逐问句",
        "",
        "| 问句 | A 均值 | B 均值 | Δ(B−A) | 平均绝对差 | ρ |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["per_question"]:
        rho_q = "n/a" if row["rho"] is None else f"{row['rho']:.3f}"
        lines.append(
            f"| {row['name']} | {row['mean_a']:.3f} | {row['mean_b']:.3f} | "
            f"{row['delta']:+.3f} | {row['mean_abs_delta']:.3f} | {rho_q} |"
        )
    lines += [
        "",
        "## 排序重合",
        "",
        "| top-K | 重合率 |",
        "|---|---|",
    ]
    for k, value in report["top"].items():
        lines.append(f"| {k} | {value:.1%} |")
    lines += [
        "",
        "## 阈值与过线率",
        "",
        "| 阈值 | A 过线率 | B 过线率 |",
        "|---|---|---|",
    ]
    for threshold in THRESHOLDS:
        lines.append(
            f"| {threshold:.2f} | {report['pass_rates']['a'][threshold]:.1%} | "
            f"{report['pass_rates']['b'][threshold]:.1%} |"
        )
    suggestion = report.get("threshold_b_for_a_045")
    lines += [
        "",
        "## 结论怎么用",
        "",
        f"- 若要让 B 的过线数与 A@0.45 相同，B 的阈值大约放在 **{suggestion}**"
        if suggestion is not None
        else "- 样本不足，无法给出阈值建议。",
        "- 这是**分布对齐**，不是正确性证明：它只保证入选规模接近，不保证入选的是同一批。",
        "- 换 provider 后先跑这份对照，再决定是否动 `state/params/` 里的阈值；不要凭感觉换。",
        "",
        "## 局限",
        "",
        "- 云端是自报分数，本地是 logit 头概率，两者不是同一种量；",
        "- 样本量小、只跑一次，没有人工标注，不能回答\"谁更准\"；",
        "- 合成分沿用当前权重，权重本身也是先验。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="对照两个判断 provider 的测量")
    parser.add_argument("--scores-a", required=True)
    parser.add_argument("--scores-b", required=True)
    parser.add_argument("--items", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--label-a", default="local")
    parser.add_argument("--label-b", default="cloud")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args(argv)

    rows_a = _load_jsonl(Path(args.scores_a))
    rows_b = _load_jsonl(Path(args.scores_b))
    items_path = Path(args.items)
    if items_path.is_dir():
        items_path = items_path / "items.json"
    items = json.loads(items_path.read_text(encoding="utf-8-sig"))
    report = compare(
        rows_a, rows_b, items,
        label_a=args.label_a, label_b=args.label_b, top=args.top,
    )
    text = render(report, scores_a_path=args.scores_a, scores_b_path=args.scores_b)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print("写入", out)
    else:
        print(text)
    if report["n"] == 0:
        return 2
    print("n={} 合成分 rho={} top10={}".format(
        report["n"],
        "n/a" if report["rho"] is None else f"{report['rho']:.3f}",
        report["top"].get(10),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
