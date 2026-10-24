"""对一批已抓取的内容跑固定的 13 个问句，产出可标定的打分行。

这一层只做"测量"：把内容喂给判断模型，原样记下每个问句的答案。
它不判断相关性、不打分、不选——那些属于下一层，且必须是确定性代码。

两个 provider（2026-10-24 起云端为默认，见 docs/05 ADR-0032）：

- `--provider cloud`（默认）：DeepSeek API，并发、重试、逐条 token 记账。
  失败写进 `failures.jsonl` 并让退出码非 0——**不允许把"没判断"写成 0 分**。
- `--provider local`：本机 Strands Decider 2B（GPU），离线或对照用。

两种 provider 写出的 `scores` 结构完全一致，下游无需知道是谁判断的。
正文为空或过短的条目不进测量：写进 `unscorable.json`，显式记为不可判。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment import cloud_judge  # noqa: E402
from judgment.questions import build_questions, to_engine_questions  # noqa: E402
from judgment.text import prepare  # noqa: E402
from tools import user_config  # noqa: E402

UNSCORABLE_REASON = "正文为空或过短，无法测量；显式记为不可判，不参与打分"

def _answers_to_scores(answers: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for name, answer in answers.items():
        if hasattr(answer, "noul"):
            scores[name] = {"noul": float(answer.noul)}
        elif hasattr(answer, "probabilities"):
            scores[name] = {"probabilities": {k: float(v) for k, v in dict(answer.probabilities).items()}}
        else:  # 未知答案形状必须显式失败，不能静默跳过
            raise TypeError(f"问句 {name} 的答案形状未知：{type(answer)!r}")
    return scores


def _provenance(item: dict[str, Any], prepared: dict[str, Any], *, raw: bool) -> dict[str, Any]:
    return {
        "source_id": item.get("source_id"),
        "domain": item.get("domain"),
        "title": item.get("title"),
        "canonical_url": item.get("canonical_url"),
        "declared_language": item.get("declared_language"),
        "detected_language": item.get("detected_language"),
        "content_digest": item.get("content_digest"),
        "prepared_chars": len(prepared["text"]),
        "used_clean_text": not raw and bool(item.get("text_clean")),
        "chrome_removed_ratio": item.get("chrome_removed_ratio"),
        **(item.get("provenance") or {}),
    }


def _row(
    item: dict[str, Any],
    profile_id: str,
    scores: dict[str, Any],
    prepared: dict[str, Any],
    *,
    raw: bool,
    judge: dict[str, Any],
) -> dict[str, Any]:
    return {
        "item_id": item["item_id"],
        "profile_id": profile_id,
        "scores": scores,
        "judge": judge,
        "provenance": _provenance(item, prepared, raw=raw),
    }


def _load_items(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        path = path / "items.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + chr(10) for row in rows), encoding="utf-8"
    )


def _write_unscorable(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": UNSCORABLE_REASON,
        "items": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")


def _run_local(
    items: list[dict[str, Any]],
    prepared: dict[str, dict[str, Any]],
    specs: list[Any],
    *,
    profile_id: str,
    model: str,
    device: str,
    raw: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """本地 decider：一次性加载权重，逐条问。失败也逐条记录，不拖垮整批。"""
    sys.path.insert(0, str(ROOT / "models" / "_tf5"))
    from strands_decider.infer import load_engine  # type: ignore

    questions = to_engine_questions(specs)
    engine = load_engine(model, device=device)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, item in enumerate(items, 1):
        prepared_text = prepared[item["item_id"]]["text"]
        started = time.time()
        try:
            answers = engine.ask(prepared_text, questions).answers
            scores = _answers_to_scores(answers)
        except Exception as exc:  # noqa: BLE001 - 单条失败必须可记录
            failures.append(
                {
                    "item_id": item["item_id"],
                    "error": {"code": "LOCAL_ERROR", "detail": str(exc)[:300]},
                }
            )
            print(f"  [{index}/{len(items)}] {item['item_id']} 失败：{str(exc)[:80]}")
            continue
        rows.append(
            _row(
                item,
                profile_id,
                scores,
                prepared[item["item_id"]],
                raw=raw,
                judge={
                    "provider": "local",
                    "model": model,
                    "device": device,
                    "attempts": 1,
                    "ms": round((time.time() - started) * 1000),
                    "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
        )
        print(f"  [{index}/{len(items)}] {item['item_id']} {(item.get('title') or '')[:32]}")
    return rows, failures


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="对抓取内容跑固定问句，产出打分行")
    parser.add_argument("--items", required=True, help="crawl 输出目录下的 items.json")
    parser.add_argument("--demand", default="latest",
                        help="需求快照；latest=取 demand/demand-2*.json 最新一份")
    parser.add_argument("--profile-id", required=True, help="写进数据行的画像名")
    parser.add_argument("--out", required=True)
    parser.add_argument("--config", default=None, help="默认 config/user.yaml")
    parser.add_argument("--provider", choices=["cloud", "local"], default=None,
                        help="覆盖 config/user.yaml 的 judge.provider")
    parser.add_argument("--model", default=None,
                        help="cloud 默认 deepseek-flash；local 默认配置里的 local_model")
    parser.add_argument("--device", default=None, help="仅 local：cuda / cpu")
    parser.add_argument("--workers", type=int, default=None,
                        help="仅 cloud：并发数，默认读配置")
    parser.add_argument("--retries", type=int, default=None,
                        help="仅 cloud：单条失败后的重试次数，默认读配置")
    parser.add_argument("--timeout", type=float, default=None,
                        help="仅 cloud：单次调用超时（秒），默认读配置")
    parser.add_argument("--thinking", choices=["disabled", "default"], default=None,
                        help="仅 cloud：默认读配置")
    parser.add_argument("--api-url", default=os.environ.get("LAYA_JUDGE_API_URL")
                        or cloud_judge.API_URL, help="仅 cloud：兼容代理或自建网关")
    parser.add_argument("--max-fail-rate", type=float, default=0.0,
                        help="失败占比超过它就以退出码 1 结束（默认 0：一条失败也不放过）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default=None, help="只跑这些 item_id，逗号分隔")
    parser.add_argument("--raw", action="store_true", help="用未去导航的原始正文（默认用去导航后的）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要发送的提示词，不调用 API")
    args = parser.parse_args(argv)

    config = user_config.load(args.config)
    problems = user_config.validate(config)
    if problems:
        print("配置有问题，先修 config/user.yaml：", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        return 2
    try:
        provider, provider_why = user_config.resolve_provider(config, args.provider)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if provider == "cloud" and args.device:
        print("注意：--device 只对本地 provider 生效，云端忽略")
    model = args.model or str(
        user_config.get(config, "judge.local_model" if provider == "local" else "judge.cloud_model")
    )
    device = args.device or str(user_config.get(config, "judge.device", "cuda"))
    workers = args.workers or int(user_config.get(config, "judge.workers", cloud_judge.DEFAULT_WORKERS))
    retries = (int(user_config.get(config, "judge.retries", cloud_judge.DEFAULT_RETRIES))
               if args.retries is None else args.retries)
    timeout = (float(user_config.get(config, "judge.timeout", cloud_judge.DEFAULT_TIMEOUT))
               if args.timeout is None else args.timeout)
    thinking = args.thinking or str(user_config.get(config, "judge.thinking", "disabled"))
    if provider == "local" and not (ROOT / model).exists():
        print(
            "本地权重不存在：{}\n"
            "要么先下载权重，要么改成云端：python tools/setup.py --set judge.provider=cloud "
            '--why "本机没有本地权重" --yes'.format(model),
            file=sys.stderr,
        )
        return 2
    print("判断：provider={}（{}）模型={}".format(provider, provider_why, model))

    try:
        demand_path = user_config.resolve_demand(config, args.demand)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    demand = json.loads(demand_path.read_text(encoding="utf-8-sig"))
    print("需求：", demand_path.relative_to(ROOT) if demand_path.is_relative_to(ROOT) else demand_path)
    specs = build_questions(demand)

    items = _load_items(Path(args.items))
    if args.only:
        wanted = {token.strip() for token in args.only.split(",")}
        items = [item for item in items if item["item_id"] in wanted]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("没有可打分的内容", file=sys.stderr)
        return 2

    prepared: dict[str, dict[str, Any]] = {}
    unscorable: list[dict[str, Any]] = []
    measurable: list[dict[str, Any]] = []
    for item in items:
        body = (item.get("text") if args.raw else item.get("text_clean")) or item.get("text") or ""
        result = prepare(body, item.get("title") or "")
        prepared[item["item_id"]] = result
        if len(result["text"].strip()) < cloud_judge.MIN_MEASURE_CHARS:
            reason = "EMPTY_TEXT" if not result["text"].strip() else "TOO_SHORT_TEXT"
            provenance = item.get("provenance") or {}
            unscorable.append(
                {
                    "item_id": item["item_id"],
                    "title": (item.get("title") or "")[:120],
                    "url": item.get("canonical_url"),
                    "source_id": item.get("source_id"),
                    "http_status": provenance.get("http_status"),
                    "prepared_chars": len(result["text"]),
                    "error": reason,
                }
            )
        else:
            measurable.append(item)

    print(
        "问句数={} 内容数={} 可测={} 不可判={}".format(
            len(specs), len(items), len(measurable), len(unscorable)
        )
    )

    if not measurable:
        print("没有可测量的内容：全部不可判，不生成打分行", file=sys.stderr)
        path = Path(args.out)
        _write_unscorable(path.parent / "unscorable.json", unscorable)
        return 2

    if args.dry_run:
        if provider != "cloud":
            print("--dry-run 只用于 cloud：本地模型没有可打印的提示词")
            return 0
        first = measurable[0]
        messages = cloud_judge.build_messages(
            specs, (first.get("title") or ""), prepared[first["item_id"]]["text"]
        )
        chars = sum(len(message["content"]) for message in messages)
        print("第一条提示词 {} 字，约 {} tokens（粗估）".format(chars, chars // 2))
        print("-" * 60)
        print(messages[-1]["content"][:4000])
        return 0

    started = time.time()
    failures: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    by_id_for_failures = {item["item_id"]: item for item in measurable}
    if provider == "cloud":
        try:
            api_key = cloud_judge.resolve_api_key()
        except cloud_judge.CloudJudgeError as exc:
            print(
                "云端判断缺少凭据：{}\n"
                "设置 DEEPSEEK_API_KEY，或改用本地：python tools/setup.py "
                '--set judge.provider=local --why "本机有本地权重" --yes'.format(exc),
                file=sys.stderr,
            )
            return 2

        def done(count: int, total: int, result: dict[str, Any]) -> None:
            if "error" in result:
                print(f"  [{count}/{total}] {result['item_id']} 失败：{result['error']['code']}")
            else:
                print(f"  [{count}/{total}] {result['item_id']}")

        results = cloud_judge.measure_batch(
            measurable,
            specs,
            prepared,
            model=model,
            api_key=api_key,
            api_url=args.api_url,
            workers=workers,
            retries=retries,
            timeout=timeout,
            thinking=thinking,
            on_done=done,
        )
        # 并发层理论上不会吞结果；真吞了也必须显式变成失败，不能靠覆盖率自己发现。
        returned = {result["item_id"] for result in results}
        for item in measurable:
            if item["item_id"] not in returned:
                failures.append(
                    {"item_id": item["item_id"],
                     "error": {"code": "MISSING_RESULT", "detail": "并发层没有返回结果"}}
                )
        measured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for result in results:
            item = by_id_for_failures[result["item_id"]]
            if "error" in result:
                failures.append(result)
                continue
            rows.append(
                _row(
                    item,
                    args.profile_id,
                    result["scores"],
                    prepared[item["item_id"]],
                    raw=args.raw,
                    judge={
                        "provider": "cloud",
                        "model": model,
                        "attempts": result["attempts"],
                        "ms": result["ms"],
                        "usage": result.get("usage") or {},
                        "note": result.get("note") or "",
                        "measured_at": measured_at,
                    },
                )
            )
        totals = cloud_judge.usage_totals(results)
        print(
            "调用完成：成功 {}，失败 {}，tokens {}（prompt {} / completion {}），{:.1f} 秒".format(
                len(rows), len(failures), totals["total_tokens"],
                totals["prompt_tokens"], totals["completion_tokens"], time.time() - started,
            )
        )
        log_rows = [
            {
                "at": measured_at,
                "item_id": result["item_id"],
                "ok": "error" not in result,
                "attempts": result.get("attempts"),
                "ms": result.get("ms"),
                "usage": result.get("usage") or {},
                "note": result.get("note") or "",
                "error": result.get("error"),
            }
            for result in results
        ]
        out = Path(args.out)
        _write_jsonl(out.parent / "judge.log.jsonl", log_rows)
    else:
        rows, failures = _run_local(
            measurable, prepared, specs, profile_id=args.profile_id, model=model,
            device=device, raw=args.raw,
        )
        print(
            "本地测量完成：成功 {}，失败 {}，{:.1f} 秒".format(
                len(rows), len(failures), time.time() - started
            )
        )

    out = Path(args.out)
    _write_jsonl(out, rows)
    _write_unscorable(out.parent / "unscorable.json", unscorable)
    if failures:
        # 失败也要能定位到内容：只留 item_id 的话，排障还得回 items.json 里翻。
        enriched = []
        measured_at_failed = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for failure in failures:
            source = by_id_for_failures.get(failure["item_id"]) or {}
            enriched.append(
                {
                    "at": measured_at_failed,
                    "item_id": failure["item_id"],
                    "title": (source.get("title") or "")[:120],
                    "url": source.get("canonical_url"),
                    "source_id": source.get("source_id"),
                    "error": failure["error"],
                }
            )
        _write_jsonl(out.parent / "failures.jsonl", enriched)

    considered = len(measurable)
    coverage = len(rows) / considered if considered else 0.0
    print(
        "覆盖：{}/{} = {:.1%}；不可判 {} 条（unscorable.json）；不可判不计入分母".format(
            len(rows), considered, coverage, len(unscorable)
        )
    )
    print("写入", out, len(rows), "行")
    if failures:
        rate = len(failures) / considered
        print("失败 {} 条，占 {:.1%}".format(len(failures), rate))
        if rate > args.max_fail_rate:
            print(
                "失败占比超过允许值 {:.1%}：不要带着缺口继续终选".format(args.max_fail_rate),
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
