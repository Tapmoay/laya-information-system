"""在一批标注数据上拟合打分参数，要求对不同用户画像都成立。

参数只有四个：价值权重、质量权重、降权惩罚、判定阈值。
拟合目标不是"总体准确率"，而是**每个画像的 F1 的平均值**，
并且要求最差画像的 F1 不低于一个下限——否则参数只对某一类用户好用。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.scoring import Weights, summarise  # noqa: E402

VALUE_GRID = (0.2, 0.3, 0.4)
DIRECTION_GRID = (0.2, 0.3, 0.4)
FLOOR_GRID = (0.15, 0.25, 0.35)
REDUCED_GRID = (0.0, 0.1, 0.2, 0.3)
THRESHOLD_GRID = tuple(round(0.40 + 0.025 * i, 3) for i in range(17))


def score_rows(rows: list[dict[str, Any]], weights: Weights) -> list[float]:
    return [summarise(row["scores"], weights)["score"] for row in rows]


def f1_at(scores: list[float], labels: list[int], threshold: float) -> float:
    tp = sum(1 for score, label in zip(scores, labels) if score >= threshold and label == 1)
    fp = sum(1 for score, label in zip(scores, labels) if score >= threshold and label == 0)
    fn = sum(1 for score, label in zip(scores, labels) if score < threshold and label == 1)
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def evaluate(
    rows: list[dict[str, Any]], weights: Weights, threshold: float
) -> dict[str, Any]:
    by_profile: dict[str, dict[str, Any]] = {}
    for profile in sorted({row["profile_id"] for row in rows}):
        subset = [row for row in rows if row["profile_id"] == profile]
        scores = score_rows(subset, weights)
        labels = [row["label"] for row in subset]
        by_profile[profile] = {
            "items": len(subset),
            "positives": sum(labels),
            "f1": round(f1_at(scores, labels, threshold), 4),
        }
    f1s = [item["f1"] for item in by_profile.values()]
    return {
        "threshold": threshold,
        "weights": {
            "direction": weights.direction,
            "value": weights.value,
            "quality": weights.quality,
            "reduced": weights.reduced,
            "direction_floor": weights.direction_floor,
        },
        "mean_f1": round(sum(f1s) / len(f1s), 4),
        "min_f1": round(min(f1s), 4),
        "by_profile": by_profile,
    }


def fit(rows: list[dict[str, Any]], *, min_f1: float = 0.6) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for direction_weight in DIRECTION_GRID:
        for value_weight in VALUE_GRID:
            quality_weight = round(1 - direction_weight - value_weight, 4)
            if quality_weight <= 0:
                continue
            for floor in FLOOR_GRID:
                for reduced in REDUCED_GRID:
                    candidate = Weights(
                        direction=direction_weight,
                        value=value_weight,
                        quality=quality_weight,
                        reduced=reduced,
                        direction_floor=floor,
                    )
                    for threshold in THRESHOLD_GRID:
                        result = evaluate(rows, candidate, threshold)
                        if result["min_f1"] < min_f1:
                            continue
                        key = (result["mean_f1"], result["min_f1"])
                        if best is None or key > (best["mean_f1"], best["min_f1"]):
                            best = result
    if best is None:
        return {"error": "没有找到满足约束的参数", "min_f1": min_f1}
    best["fitted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    best["dataset_size"] = len(rows)
    best["profiles"] = sorted({row["profile_id"] for row in rows})
    return best


def split_rows(
    rows: list[dict[str, Any]], holdout_ratio: float = 0.3, seed: int = 0
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 item_id 切分，保证同一条内容不会同时出现在训练集和留出集。"""
    import random

    item_ids = sorted({row["item_id"] for row in rows})
    random.Random(seed).shuffle(item_ids)
    count = max(1, int(len(item_ids) * holdout_ratio))
    holdout_ids = set(item_ids[:count])
    train = [row for row in rows if row["item_id"] not in holdout_ids]
    holdout = [row for row in rows if row["item_id"] in holdout_ids]
    return train, holdout


def cross_validate(
    rows: list[dict[str, Any]],
    *,
    folds: int = 5,
    min_f1: float = 0.0,
) -> dict[str, Any]:
    """按 item_id 做 k 折交叉验证，报告每个画像的 F1 均值与标准差。

    单次留出在小样本上毫无意义：同一批数据换个种子，F1 就能从 0 跳到 0.8。
    交叉验证至少能量化这种抖动。
    """
    import statistics

    item_ids = sorted({row["item_id"] for row in rows})
    folds = max(2, min(folds, len(item_ids)))
    buckets: list[list[str]] = [[] for _ in range(folds)]
    for index, item_id in enumerate(item_ids):
        buckets[index % folds].append(item_id)

    per_profile: dict[str, list[float]] = {}
    for bucket in buckets:
        held = set(bucket)
        train = [row for row in rows if row["item_id"] not in held]
        test = [row for row in rows if row["item_id"] in held]
        if not train or not test:
            continue
        params = fit(train, min_f1=min_f1)
        if "error" in params:
            continue
        result = evaluate(test, Weights(**params["weights"]), params["threshold"])
        for profile, payload in result["by_profile"].items():
            per_profile.setdefault(profile, []).append(payload["f1"])

    summary = {}
    for profile, values in per_profile.items():
        summary[profile] = {
            "folds": len(values),
            "mean_f1": round(statistics.fmean(values), 4),
            "stdev_f1": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
            "min_f1": round(min(values), 4),
            "max_f1": round(max(values), 4),
        }
    return {"folds": folds, "by_profile": summary}


def fit_with_validation(
    rows: list[dict[str, Any]],
    *,
    min_f1: float = 0.0,
    holdout_ratio: float = 0.3,
    seed: int = 0,
) -> dict[str, Any]:
    train, holdout = split_rows(rows, holdout_ratio, seed)
    best = fit(train, min_f1=min_f1)
    if "error" in best:
        return best
    weights = Weights(**best["weights"])
    best["holdout"] = evaluate(holdout, weights, best["threshold"])
    best["holdout_items"] = len({row["item_id"] for row in holdout})
    best["train_rows"] = len(train)
    return best


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="拟合打分参数")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", default="calibration/params.json")
    parser.add_argument("--min-f1", type=float, default=0.6)
    parser.add_argument("--holdout", type=float, default=0.0, help="留出比例（按 item_id 切）")
    parser.add_argument("--cv", type=int, default=0, help="k 折交叉验证")
    args = parser.parse_args(argv)

    from calibration.dataset import load

    rows = load(args.dataset)
    if args.cv > 1:
        result = {"cross_validation": cross_validate(rows, folds=args.cv), **fit(rows, min_f1=args.min_f1)}
    elif args.holdout > 0:
        result = fit_with_validation(rows, min_f1=args.min_f1, holdout_ratio=args.holdout)
    else:
        result = fit(rows, min_f1=args.min_f1)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
