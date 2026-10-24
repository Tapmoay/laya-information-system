"""受控发现：你把看到的链接扔进来，系统判断它值不值得占位。

这是「把 B站、YouTube、论坛这些你控制不了也抓不全的渠道接进来」的唯一方式——
不把它们做成常规来源（密度低、要 JS 渲染），而是**你看到什么，我判什么**。

三步：

1. 抓这一页。拿不到可读正文就当场说清楚（多半是 JS 渲染），**不拿空壳去打分**。
2. 跑同一套 13 问，得到分数与各槽分。
3. 给出：哪条通道、哪个状态、为什么。

    python tools/evaluate_link.py https://example.com/some-article
    python tools/evaluate_link.py https://example.com/some-article --provider cloud
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest.fetch import FetchPolicy, fetch_page  # noqa: E402
from ingest.urls import canonicalize_url, host_of  # noqa: E402
from judgment import cloud_judge  # noqa: E402
from judgment.channels import CHANNELS, assign, decide, label  # noqa: E402
from judgment.questions import build_questions, to_engine_questions  # noqa: E402
from judgment.recency import measure as measure_recency  # noqa: E402
from judgment.scoring import summarise  # noqa: E402
from judgment.text import prepare  # noqa: E402
from tools import user_config  # noqa: E402

MIN_TEXT_CHARS = 200


def _answers_to_scores(answers: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for name, answer in answers.items():
        if hasattr(answer, "noul"):
            scores[name] = {"noul": float(answer.noul)}
        elif hasattr(answer, "probabilities"):
            scores[name] = {"probabilities": {k: float(v) for k, v in dict(answer.probabilities).items()}}
        else:
            raise TypeError("问句 {} 的答案形状未知：{}".format(name, type(answer)))
    return scores


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="手动投喂一条链接，判断它值不值得占位")
    parser.add_argument("url")
    parser.add_argument("--config", default=None, help="默认 config/user.yaml")
    parser.add_argument("--demand", default="latest", help="需求快照；默认最新一份")
    parser.add_argument("--provider", choices=["cloud", "local"], default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args(argv)

    config = user_config.load(args.config)
    provider, provider_why = user_config.resolve_provider(config, args.provider)
    proxy = args.proxy or str(
        user_config.get(config, "crawl.proxy")
        or user_config.get(config, "proxy.url")
        or ""
    ) or None

    host = host_of(args.url)
    bare = ".".join(host.split(".")[-2:]) if host.count(".") >= 2 else host
    policy = FetchPolicy(
        allowed_hosts=(host, bare),
        timeout_seconds=args.timeout,
        max_attempts=2,
        max_bytes=4_000_000,
        respect_robots=True,
        user_agent="LAYA-personal-information/0.1",
        proxy=proxy,
    )
    now = datetime.now(timezone.utc)
    fetched = fetch_page(args.url, request_id="link-" + hashlib.sha256(args.url.encode()).hexdigest()[:10], policy=policy, now=now)
    result = fetched["result"]
    if result["outcome"] == "ERROR":
        print("抓不到：" + json.dumps(result.get("error"), ensure_ascii=False))
        return 2

    title = result.get("title") or ""
    body = result.get("text") or ""
    prepared = prepare(body, title)
    if len(prepared["text"]) < MIN_TEXT_CHARS:
        # 这一步是关键：B站、YouTube 这类页面抓回来是个空壳，
        # 拿它去打分只会得到一堆噪声。宁可说"拿不到"，也不假装判断过。
        print("拿不到可读正文（{} 字）。这类页面多半靠 JS 渲染，换个能直接读的地址，"
              "或者把标题和要点贴给我。".format(len(prepared["text"])))
        return 3

    canonical = canonicalize_url(result["final_url"])
    item = {
        "item_id": "link-" + hashlib.sha256(canonical.encode()).hexdigest()[:12],
        "title": title,
        "canonical_url": canonical,
        "text_clean": prepared["text"],
        "detected_language": result.get("declared_language"),
        "source_id": "MANUAL",
    }

    demand_path = user_config.resolve_demand(config, args.demand)
    demand = json.loads(demand_path.read_text(encoding="utf-8-sig"))
    specs = build_questions(demand)
    print("判断：provider={}（{}）需求={}".format(provider, provider_why, demand_path.name))

    if provider == "cloud":
        try:
            key = cloud_judge.resolve_api_key()
        except cloud_judge.CloudJudgeError as exc:
            print("云端判断缺少凭据：" + str(exc), file=sys.stderr)
            return 2
        try:
            output = cloud_judge.judge(
                specs,
                title,
                prepared["text"],
                model=args.model or str(user_config.get(config, "judge.cloud_model")),
                api_key=key,
                timeout=float(user_config.get(config, "judge.timeout", 90)),
                retries=int(user_config.get(config, "judge.retries", 2)),
                thinking=str(user_config.get(config, "judge.thinking", "disabled")),
            )
        except cloud_judge.CloudJudgeError as exc:
            print("测量失败：" + str(exc), file=sys.stderr)
            return 2
        scores = output.scores
    else:
        model = args.model or str(user_config.get(config, "judge.local_model"))
        device = args.device or str(user_config.get(config, "judge.device", "cuda"))
        if not (ROOT / model).exists():
            print("本地权重不存在：{}；可加 --provider cloud".format(model), file=sys.stderr)
            return 2
        questions = to_engine_questions(specs)

        import sys as _sys

        _sys.path.insert(0, str(ROOT / "models" / "_tf5"))
        from strands_decider.infer import load_engine

        engine = load_engine(model, device=device)
        answers = engine.ask(prepared["text"], questions).answers
        scores = _answers_to_scores(answers)
    recency = measure_recency(item, now)
    summary = summarise(scores, age_days=recency["age_days"], text=prepared["text"])
    channel = assign(summary["values"])
    state = decide(summary["score"])

    payload = {
        "item": item,
        "scores": scores,
        "summary": summary,
        "recency": recency,
        "channel": channel,
        "state": state,
        "state_label": label(state),
        "evaluated_at": now.isoformat(timespec="seconds"),
    }
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print("标题：" + title[:80])
    print("分数：{:.3f}　状态：{}　通道：{}（{}）".format(
        summary["score"], label(state), channel["channel"], CHANNELS[channel["channel"]]))
    print("通道分：A {A:.2f} / B {B:.2f} / C {C:.2f}".format(**channel["scores"]))
    if recency["age_days"] is not None:
        print("时间：{} 天前（{}，{}）".format(recency["age_days"], recency["source"], recency["precision"]))
    else:
        print("时间：未知（只标记，不按「旧」处理）")
    if summary["reduced"]:
        print("形态：{}（降权；发布类例外={}）".format(summary["form"], summary["release_exception"]))
    print("")
    print("要不要它：")
    if state == "DEEP":
        print("  值得深入——它过了深入线，可以占一个位置。")
    elif state == "SAVE":
        print("  值得暂存——先存链接，别现在读。")
    else:
        print("  直接放弃——分数没过线。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
