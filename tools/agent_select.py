"""Agent 终选：生成终选包 → 读回决策 → 校验并落盘。

用法：
  python tools/agent_select.py --build --scores calibration/scores-crawl-ai.jsonl \
      --items runs/crawl-20261018T080741Z/items.json --demand demand/demand-2026-01-01.json \
      --out runs/agent/2026-10-18
  # 填 out/decision.json 之后
  python tools/agent_select.py --apply --out runs/agent/2026-10-18 \
      --scores calibration/scores-crawl-ai.jsonl --items runs/crawl-20261018T080741Z/items.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.agent_select import apply_decision, build_packet, decision_template, override_events  # noqa: E402
from tools import user_config  # noqa: E402

# 参数版本库的默认位置：与 tools/learn.py 的 --base 默认值一致。
# 缺文件不是错误——v0000 就是先验，每日链路必须能在"还没学过任何东西"的机器上跑。
DEFAULT_PARAMS_ROOT = ROOT / "state" / "params"
DEFAULT_USER = "me"
DEFAULT_EVENT_LOG = ROOT / "store" / "event-log.jsonl"


def _clean_title(text: str) -> str:
    """表格里的标题要能一格放下：去掉站点后缀，竖线换成全角。"""
    title = (text or "").split(" | ")[0].strip()
    return title.replace("|", "／")[:44]


def _summaries(candidates: list) -> dict[str, str]:
    """复核要能判断，就必须看到摘要——只有标题等于逼人拿标题猜内容。"""
    from judgment.text import summarise as summarise_text

    result: dict[str, str] = {}
    for row in candidates:
        title = (row.get("provenance") or {}).get("title") or ""
        body = row.get("body") or ""
        if body:
            result[row["item_id"]] = summarise_text(body, title, 320)
    return result


def _write_review(out: Path, candidates: list, check) -> Path:
    """给用户看的复核表：只列「做了什么决定」，不列没被判断过的条目。"""
    titles = {(row.get("provenance") or {}).get("title") or row["item_id"]: row for row in candidates}
    by_id = {row["item_id"]: row for row in candidates}
    lines = [
        "# 终选复核表",
        "",
        "**判的是内容，不是 Agent。** 只回答一句：这条内容要不要进最终推荐？",
        "",
        "写「要」/「不要」/「说不准」。不要去想「Agent 选得对不对」——",
        "那个由答案反推：选中的标「不要」= 误选，排除的标「要」= 漏选。",
        "",
        "## 一、Agent 选中的（{} 条）".format(len(check.final["core"]) + len(check.final["exploration"])),
        "",
        "| # | 位置 | 条目 | 理由 | 判断 |",
        "|---|---|---|---|---|",
    ]
    index = 0
    for slot, key in (("核心", "core"), ("探索", "exploration")):
        for entry in check.final[key]:
            index += 1
            title = _clean_title((by_id[entry["item_id"]].get("provenance") or {}).get("title") or "")
            lines.append("| {} | {} | {} | {} | 对 / 不对： |".format(index, slot, title, entry["reason"]))
    lines += [
        "",
        "## 二、Agent 排除的（按粗分从高到低）",
        "",
        "| # | 条目 | 粗分 | 排除理由 | 判断 |",
        "|---|---|---|---|---|",
    ]
    for offset, entry in enumerate(check.final["excluded"], 1):
        row = by_id[entry["item_id"]]
        title = _clean_title((row.get("provenance") or {}).get("title") or "")
        lines.append(
            "| {} | {} | {:.3f} | {} | 对 / 不对： |".format(
                offset, title, float(row.get("score", 0.0)), entry["reason"]
            )
        )
    lines += [
        "",
        "## 三、没被判断的",
        "",
        "候选池里剩下的条目既没被选、也没被明确排除——它们属于「看过但没表态」。",
        "不表态不会产生任何反馈，这是刻意的：只有明确的取舍才是证据。",
        "",
    ]
    summaries = _summaries(candidates)
    lines += ["## 附：各条摘要", ""]
    for entry in check.final["core"] + check.final["exploration"] + check.final["excluded"]:
        title = _clean_title((by_id[entry["item_id"]].get("provenance") or {}).get("title") or "")
        lines.append("**" + title + "**")
        lines.append("")
        lines.append("> " + (summaries.get(entry["item_id"]) or "（没有抓到正文）"))
        lines.append("")

    path = out / "review.md"
    path.write_text(chr(10).join(lines), encoding="utf-8")
    return path
from judgment.recency import measure as measure_recency  # noqa: E402
from judgment.review_page import render as render_review  # noqa: E402
from judgment.labels import goal_labels  # noqa: E402
from judgment.scoring import DEFAULT_WEIGHTS, rank, select, summarise  # noqa: E402
from judgment.params import PRIOR, Parameters, ParameterStore  # noqa: E402


def _load_parameters(root: Path | str, user: str):
    """读当前参数版本；没有版本时返回 v0000（先验），不报错。"""
    return ParameterStore(root, user).current()


# 候选池：最多 30 条 + 最多 3 条「重大事件候选」。
# 事件候选不按分数进：它们可能和主目标没关系，却会改变对世界的判断（前沿模型发布、
# 规则变化、影响成本结构的突破）。环境价值只是把它们**递到眼前**，是不是重大事件由 Agent 定。
POOL_LIMIT = 30
EVENT_SLOTS = 3
EVENT_ENV_FLOOR = 0.75


def _event_candidates(
    rows: list[dict[str, Any]], *, exclude: set[str], limit: int
) -> list[dict[str, Any]]:
    """按环境价值挑事件候选；降权项不占这个位置（§2.3 的营销/招聘不该借事件还魂）。"""
    picked: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if row["item_id"] in exclude:
            continue
        if row.get("reduced") or row.get("hard_drop"):
            continue
        env = float((row.get("values") or {}).get("value_environment", 0.0))
        if env < EVENT_ENV_FLOOR:
            continue
        picked.append((env, row))
    picked.sort(key=lambda pair: (-pair[0], pair[1]["item_id"]))
    return [row for _, row in picked[:limit]]


def _write_params_audit(out: Path, current, *, threshold: float, threshold_from: str) -> Path:
    """把"这次用的是哪个参数版本"落进运行产物。

    三个月后要能回答"为什么那天选的是这几条"——参数版本是答案的一半。
    """
    payload = {
        "version": current.version,
        "digest": current.parameters.digest(),
        "source": current.source,
        "updated_at": current.updated_at,
        "parameters": current.parameters.as_dict(),
        "threshold": round(float(threshold), 6),
        "threshold_from": threshold_from,
    }
    path = out / "params.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    return path


def _write_event_log(
    path: Path,
    candidates: list[dict[str, Any]],
    final: dict[str, Any],
    *,
    run_date: str,
    params_version: int,
) -> Path:
    """把「事件候选」与「被判成重大事件」的条目落成可追踪日志。

    每天都要回答同一个问题：这次推的"重大事件"到底重不重大？
    没有日志时只能靠记忆；有了它，一周后可以逐条回看，也能算出
    "事件候选里有几条真的被判成重大"——这是这个位置有没有用的唯一证据。
    """
    picked: dict[str, dict[str, Any]] = {}
    for key in ("core", "exploration"):
        for entry in final.get(key) or []:
            picked[entry["item_id"]] = {**entry, "slot": key}
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for row in candidates:
        entry = picked.get(row["item_id"])
        if not row.get("event_slot") and not (entry and entry.get("major_event")):
            continue
        rows.append(
            {
                "at": at,
                "date": run_date,
                "item_id": row["item_id"],
                "title": row.get("title") or "",
                "url": row.get("url") or "",
                "score": round(float(row.get("score", 0.0)), 4),
                "env": round(float((row.get("values") or {}).get("value_environment", 0.0)), 4),
                "event_candidate": bool(row.get("event_slot")),
                "picked": bool(entry),
                "slot": entry.get("slot") if entry else None,
                "major": bool(entry and entry.get("major_event")),
                "reason": (entry or {}).get("reason", ""),
                "event_reason": (entry or {}).get("event_reason", ""),
                "params_version": params_version,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # 同一天重跑 --apply 时以最后一次为准：删掉这一天的旧行再写。
    kept: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("date") != run_date:
                kept.append(payload)
    with path.open("w", encoding="utf-8") as handle:
        for payload in kept + rows:
            handle.write(json.dumps(payload, ensure_ascii=False) + chr(10))
    return path


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


VISUAL_FIELDS = {"design", "art", "architecture"}
VISUAL_SLOTS = 3
PER_SOURCE_CAP = 2


def _cap_per_source(rows: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    seen: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for row in rows:
        source = (row.get("provenance") or {}).get("source_id") or row.get("domain") or row["item_id"]
        if seen.get(source, 0) >= cap:
            continue
        seen[source] = seen.get(source, 0) + 1
        kept.append(row)
    return kept


def _visual_candidates(
    ordered: list[dict[str, Any]], *, exclude: set[str], limit: int
) -> list[dict[str, Any]]:
    """按分数挑视觉类候选，但只用排行里排过的条目（不含硬排除项）。"""
    picked: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    for row in ordered:
        if len(picked) >= limit:
            break
        if row["item_id"] in exclude:
            continue
        if row.get("hard_drop"):
            continue
        if row.get("field") not in VISUAL_FIELDS:
            continue
        # 一个源只占一个视觉席位：否则三个席位会全被同一个建筑站包掉，
        # "扩大接触面"就变成了"同一个站看三天"。
        source = (row.get("provenance") or {}).get("source_id") or row.get("domain") or ""
        if source in used_sources:
            continue
        used_sources.add(source)
        picked.append(row)
    return picked


def _candidates(
    scores_path: Path,
    items_path: Path,
    *,
    limit: int,
    threshold: float | None = None,
    weights=None,
) -> list[dict[str, Any]]:
    items = json.loads(items_path.read_text(encoding="utf-8-sig"))
    by_id = {item["item_id"]: item for item in items}
    # 条目的 field 不在 items.json 里，得回登记表按 source_id 查
    try:
        from ingest.source_index import load as _load_sources

        _field_of = {src.id: src.field for src in _load_sources(ROOT / "config" / "sources-index.yaml")["sources"]}
    except Exception:
        _field_of = {}
    effective_weights = weights if weights is not None else DEFAULT_WEIGHTS
    rows = []
    for row in _load_jsonl(scores_path):
        item = by_id.get(row["item_id"]) or {}
        item_id = row["item_id"]
        observed_raw = (item.get("provenance") or {}).get("index_fetched_at")
        try:
            observed = datetime.fromisoformat(observed_raw) if observed_raw else datetime.now(timezone.utc)
        except ValueError:
            observed = datetime.now(timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        recency = measure_recency(item, observed)
        body = item.get("text_clean") or item.get("text") or ""
        summary = summarise(
            row["scores"],
            weights=effective_weights,
            age_days=recency["age_days"],
            text=body,
            title=item.get("title") or "",
        )
        rows.append(
            {
                "item_id": item_id,
                "title": (row.get("provenance") or {}).get("title", "") or item.get("title", ""),
                "provenance": row.get("provenance") or {},
                "domain": (row.get("provenance") or {}).get("domain", "") or item.get("domain", ""),
                "field": _field_of.get(item.get("source_id"), ""),
                "url": item.get("canonical_url"),
                "scores": row["scores"],
                "score": summary["score"],
                "reduced": summary["reduced"],
                "hard_drop": summary["hard_drop"],
                "values": summary["values"],
                "labels": goal_labels(summary["values"]),
                "age_days": recency["age_days"],
                "recency": recency,
                "body": body,
                "images": item.get("images") or [],
            }
        )
    ordered = rank(rows)
    if threshold is None:
        threshold = Parameters(**PRIOR).threshold
    chosen = select(ordered, threshold=threshold)
    picked = {item["item_id"] for item in chosen["core"] + chosen["exploration"]}
    for row in ordered:
        row["selected"] = row["item_id"] in picked
    above = [row for row in ordered if row["score"] >= threshold]
    base = above if above else ordered
    # 同一个源最多占两席：三十个席位里五个来自量子位，候选池就退化成"一个站的一天"。
    base = _cap_per_source(base, PER_SOURCE_CAP)
    # 先给事件候选留 3 个位置，再按分数补满——重大事件可能分不高，但不能看不见。
    head = base[: max(0, limit - EVENT_SLOTS - VISUAL_SLOTS)]
    events = _event_candidates(ordered, exclude={row["item_id"] for row in head}, limit=EVENT_SLOTS)
    for row in events:
        row["event_slot"] = True
    # 示例第二方向席位：设计/艺术/建筑类源常常整体分偏低，纯按分数排就永远进不了候选池，
    # 于是 C 通道只能从"恰好分高的那个源"里挑。这里给它们留固定席位（不保证被选）。
    # 装配顺序决定谁是"被保留的"：视觉席位优先，其次事件，最后按分数填。
    # 全程受"每源最多两席"约束——否则视觉席位会被同一个建筑站的前两条吃掉。
    result: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    taken: set[str] = set()

    def _source(row: dict[str, Any]) -> str:
        return (row.get("provenance") or {}).get("source_id") or row.get("domain") or row["item_id"]

    def _add(row: dict[str, Any]) -> bool:
        if row["item_id"] in taken:
            return False
        source = _source(row)
        if counts.get(source, 0) >= PER_SOURCE_CAP:
            return False
        counts[source] = counts.get(source, 0) + 1
        taken.add(row["item_id"])
        result.append(row)
        return True

    for row in _visual_candidates(ordered, exclude=set(), limit=VISUAL_SLOTS):
        if _add(row):
            row["visual_slot"] = True
    for row in _event_candidates(ordered, exclude=taken, limit=EVENT_SLOTS):
        if _add(row):
            row["event_slot"] = True
    for row in head:
        if len(result) >= limit:
            break
        _add(row)
    if len(result) < limit:
        for row in base:
            if len(result) >= limit:
                break
            _add(row)
    return result[:limit]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Agent 终选：终选包与决策校验")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--scores", required=True)
    parser.add_argument("--items", required=True)
    parser.add_argument("--demand", default=None,
                        help="需求快照；默认读 config/user.yaml 或 demand/ 下最新日期快照")
    parser.add_argument("--config", default=None, help="默认 config/user.yaml")
    parser.add_argument("--policy", default="information-policy.md", help="判断依据（政策文档），会节选进终选包")
    parser.add_argument("--out", default="runs/agent/latest")
    parser.add_argument("--limit", type=int, default=POOL_LIMIT, help="候选池上限（事件候选占其中 3 席）")
    parser.add_argument("--coarse", type=float, default=None, help="粗选阈值；默认取当前参数版本里的 threshold")
    parser.add_argument("--params", default=str(DEFAULT_PARAMS_ROOT), help="参数版本库目录")
    parser.add_argument("--user", default=DEFAULT_USER, help="参数版本库里的用户目录")
    parser.add_argument("--event-log", default=str(DEFAULT_EVENT_LOG), help="重大事件候选与判定日志")
    parser.add_argument("--date", default=None, help="日志里的日期，默认今天")
    args = parser.parse_args(argv)

    scores_path = Path(args.scores)
    items_path = Path(args.items)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    current = _load_parameters(args.params, args.user)
    weights = current.parameters.to_weights()
    if args.coarse is not None:
        threshold, threshold_from = args.coarse, "cli"
    else:
        threshold, threshold_from = current.parameters.threshold, "params"
    print(
        "参数：v{:04d} {} 阈值 {:.3f}（{}）权重 {}".format(
            current.version,
            current.source,
            threshold,
            threshold_from,
            "/".join(
                "{:.2f}".format(value)
                for value in (weights.direction, weights.value, weights.quality, weights.exploration)
            ),
        )
    )
    candidates = _candidates(
        scores_path, items_path, limit=args.limit, threshold=threshold, weights=weights
    )
    audit_path = _write_params_audit(out, current, threshold=threshold, threshold_from=threshold_from)

    config = user_config.load(args.config)
    demand_path: Path | None = None
    try:
        demand_path = user_config.resolve_demand(config, args.demand)
    except FileNotFoundError as exc:
        if args.build:
            print(str(exc), file=sys.stderr)
            return 2

    if args.build:
        if demand_path is None:
            print("--build 需要需求快照", file=sys.stderr)
            return 2
        demand = json.loads(demand_path.read_text(encoding="utf-8-sig"))
        print("需求：", demand_path.relative_to(ROOT) if demand_path.is_relative_to(ROOT) else demand_path)
        bodies = {row["item_id"]: row["body"] for row in candidates}
        policy_text = ""
        policy_path = Path(args.policy)
        if policy_path.exists():
            policy_text = policy_path.read_text(encoding="utf-8")
        packet = build_packet(candidates, demand, bodies=bodies, policy=policy_text)
        (out / "packet.md").write_text(packet, encoding="utf-8")
        template = decision_template(candidates)
        (out / "decision.json").write_text(
            json.dumps(template, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8"
        )
        (out / "candidates.json").write_text(
            json.dumps(
                [{k: v for k, v in row.items() if k != "body"} for row in candidates],
                ensure_ascii=False,
                indent=2,
            )
            + chr(10),
            encoding="utf-8",
        )
        print("终选包：", out / "packet.md", "候选", len(candidates), "条")
        print("参数留痕：", audit_path)
        print("待填决策：", out / "decision.json")
        return 0

    if args.apply:
        decision_path = out / "decision.json"
        if not decision_path.exists():
            print("找不到 decision.json", file=sys.stderr)
            return 2
        decision = json.loads(decision_path.read_text(encoding="utf-8-sig"))
        check = apply_decision(candidates, decision, summaries=_summaries(candidates))
        if not check.ok:
            for problem in check.violations:
                print("不合法：", problem, file=sys.stderr)
            return 1
        events = override_events(check, candidates)
        (out / "selection.json").write_text(
            json.dumps({"selection": check.final, "override_events": events}, ensure_ascii=False, indent=2)
            + chr(10),
            encoding="utf-8",
        )
        print("核心", len(check.final["core"]), "条，探索", len(check.final["exploration"]),
              "条，排除", len(check.final["excluded"]), "条")
        log_path = _write_event_log(
            Path(args.event_log),
            candidates,
            check.final,
            run_date=args.date or datetime.now().strftime("%Y-%m-%d"),
            params_version=current.version,
        )
        print("事件日志：", log_path)
        print("写入", out / "selection.json")
        print("复核表", _write_review(out, candidates, check))
        demand_data = None
        if demand_path is not None:
            demand_data = json.loads(demand_path.read_text(encoding="utf-8-sig"))
        html_path = out / "review.html"
        html_path.write_text(
            render_review(
                candidates,
                check.final,
                demand=demand_data,
                summaries=_summaries(candidates),
                images={row["item_id"]: row.get("images") or [] for row in candidates},
                generated_at=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
            ),
            encoding="utf-8",
        )
        print("网页版", html_path)
        return 0

    print("需要 --build 或 --apply", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
