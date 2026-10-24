"""按来源登记表抓内容，并留下可回溯的取证链。

每条内容必须能回答四个问题：
  1) 从哪个列表页发现的（index_url + 抓取时间 + 列表排序位置）
  2) 依据什么规则认定它是文章链接（path + pattern）
  3) 正文是什么时候、从哪个最终地址抓的（requested_url / final_url / http_status）
  4) 抓到的东西有没有被改过（content digest + 原始 HTML 落盘位置）

少了任何一条，"这条内容是真的"就只是一句声明。
"""

from __future__ import annotations

import argparse
import hashlib
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

from ingest.discover import discover  # noqa: E402
from judgment.denoise import apply as denoise_apply  # noqa: E402
from judgment.embeddings import available as embeddings_available, embed_items  # noqa: E402
from judgment.events import cluster  # noqa: E402
from judgment.images import extract_images  # noqa: E402
from ingest.fetch import FetchPolicy, fetch_page  # noqa: E402
from ingest.html_extract import derive_title  # noqa: E402
from ingest.normalize import build_candidate  # noqa: E402
from ingest.source_index import Source, load  # noqa: E402
from ingest.urls import canonicalize_url, host_of  # noqa: E402
from tools import user_config  # noqa: E402


def item_id_for(url: str) -> str:
    """稳定的条目 id：同一条内容重抓仍是同一个 id，标注与反馈不会因为重抓而断链。"""
    canonical = canonicalize_url(url)
    return "item-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def resolve_title(page_title: str | None, text: str, anchor: str) -> tuple[str | None, str]:
    """决定这条内容用什么当标题，并标明来源。

    三条退路，按可信度从高到低：页面标题 → 正文首句 → 列表页链接文字。
    都没有就返回 None：宁可记一条失败，也不要编一个标题出来。
    """
    page_title = (page_title or "").strip()
    if page_title:
        return page_title, "PAGE"
    derived = derive_title(text)
    if derived:
        return derived, "BODY"
    anchor = (anchor or "").strip()
    if 6 <= len(anchor) <= 120:
        return anchor, "ANCHOR"
    return None, "MISSING"


def proxy_alive(proxy: str, timeout: float = 3.0) -> bool:
    """代理配了不等于代理能用。探一次端口，不通就当作没配。

    为什么要探：代理挂了的时候，每个走代理的源都要等一次超时才记 NEEDS_PROXY，
    一次日常运行能被拖到一小时。探一次只要三秒。
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(proxy if "://" in proxy else "http://" + proxy)
    host, port = parsed.hostname, parsed.port or 80
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _policy_for(
    url: str, options: dict[str, Any], extra_hosts: tuple[str, ...], proxy: str | None = None
) -> FetchPolicy:
    host = host_of(url)
    bare = ".".join(host.split(".")[-2:]) if host.count(".") >= 2 else host
    hosts = tuple(dict.fromkeys((host, bare, *extra_hosts)))
    return FetchPolicy(
        allowed_hosts=hosts,
        timeout_seconds=float(options["timeout_seconds"]),
        max_attempts=int(options["max_attempts"]),
        max_bytes=int(options["max_bytes"]),
        respect_robots=bool(options["respect_robots"]),
        user_agent=str(options["user_agent"]),
        proxy=proxy,
    )


def crawl_source(
    source: Source,
    *,
    out: Path,
    now: datetime,
    per_source: int | None = None,
    seen_urls: set[str] | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    seen_urls = seen_urls if seen_urls is not None else set()
    budget = per_source if per_source is not None else source.per_source
    record: dict[str, Any] = {
        "source_id": source.id,
        "name": source.name,
        "domain": source.domain,
        "declared_language": source.language,
        "status": source.status,
        "index_pages": [],
        "items": [],
        "failures": [],
        "skipped_duplicates": 0,
    }

    # 出口需求写成数据之后，缺代理就不该表现为"抓不到"：那两者补救方式完全不同。
    if source.network == "proxy" and not proxy:
        for page in source.pages:
            record["failures"].append(
                {"stage": "INDEX", "url": page.url, "reason_code": "NEEDS_PROXY"}
            )
        return record

    links: list[dict[str, Any]] = []
    consecutive_failures = 0
    # 出口按源决定：直连源不该被全局代理拖下水（实测少数派走代理 1 秒内就失败，
    # 直连 2.4 秒拿到条目）。直连失败时才回退到代理。
    active_proxy = proxy if source.network == "proxy" else None
    for page in source.pages:
        policy = _policy_for(page.url, source.options, (), active_proxy)
        fetched = fetch_page(
            page.url, request_id="index-" + source.id + "-" + str(len(record["index_pages"])), policy=policy, now=now
        )
        result = fetched["result"]
        if result["outcome"] == "ERROR" and active_proxy is None and proxy:
            # 直连不通、但有代理：试一次再决定这个源要不要放弃
            policy = _policy_for(page.url, source.options, (), proxy)
            fetched = fetch_page(
                page.url, request_id="index-" + source.id + "-proxy-" + str(len(record["index_pages"])), policy=policy, now=now
            )
            result = fetched["result"]
            if result["outcome"] != "ERROR":
                active_proxy = proxy
        if result["outcome"] == "ERROR":
            consecutive_failures += 1
            reason = str((result.get("error") or {}).get("reason_code") or "")
            hopeless = reason in {"TIMEOUT", "SOURCE_UNREACHABLE", "TLS_BLOCKED", "NETWORK"}
            if hopeless or consecutive_failures >= 2:
                record["failures"].append(
                    {"stage": "INDEX", "url": page.url, "reason_code": "SOURCE_ABORTED"}
                )
                break
            record["index_pages"].append(
                {
                    "index_url": page.url,
                    "outcome": "ERROR",
                    "reason_code": result["error"]["reason_code"],
                    "fetched_at": result["fetched_at"],
                }
            )
            record["failures"].append(
                {"stage": "INDEX", "url": page.url, "reason_code": result["error"]["reason_code"]}
            )
            continue
        # CLI 的 --per-source 只放大抓取预算是不够的：发现阶段的规则本身也有上限，
        # 不一起放大就永远只看列表第一屏，抓到的永远是同一批。
        rule = page.rule
        if budget > rule.limit:
            from dataclasses import replace as _replace

            rule = _replace(rule, limit=budget)
        found = discover(page.url, fetched["html"] or "", rule)
        record["index_pages"].append(
            {
                "index_url": page.url,
                "final_url": result["final_url"],
                "outcome": result["outcome"],
                "http_status": result["status_code"],
                "fetched_at": result["fetched_at"],
                "rule": found["rule"],
                "counts": found["counts"],
            }
        )
        if found["counts"]["selected"] == 0:
            # 列表页打开了却一条都没命中：这不是"今天没更新"。
            # 要么是我们对"这个站的文章长什么样"的断言失效了，
            # 要么站点回了一个空壳（实测 Check Point 会回 202 挑战页）。
            # 不记下来，它会以"items=0 fail=0"的形式消失——最坏的一种失败。
            record["failures"].append(
                {
                    "stage": "INDEX",
                    "url": page.url,
                    "reason_code": "INDEX_EMPTY",
                    "http_status": result["status_code"],
                    "anchors": found["counts"]["anchors"],
                }
            )
        for link in found["links"]:
            links.append({**link, "index_url": page.url, "index_fetched_at": result["fetched_at"]})

    for link in links:
        if len(record["items"]) >= budget:
            break
        url = link["canonical_url"]
        if url in seen_urls:
            record["skipped_duplicates"] += 1
            continue
        seen_urls.add(url)
        item_id = item_id_for(url)
        # 代理必须一路带到正文抓取：只给列表页加代理，会出现"列表通了、文章全挂"，
        # 而且失败理由是 SOURCE_UNREACHABLE——看起来像站点问题，实际是自己漏传了参数。
        policy = _policy_for(url, source.options, (), active_proxy)
        fetched = fetch_page(url, request_id="art-" + item_id, policy=policy, now=now)
        result = fetched["result"]
        if result["outcome"] == "ERROR":
            record["failures"].append(
                {"stage": "ARTICLE", "url": url, "reason_code": result["error"]["reason_code"]}
            )
            continue
        raw_dir = out / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / (item_id + ".html")
        raw_path.write_text(fetched["html"] or "", encoding="utf-8")
        resolved, title_source = resolve_title(
            result.get("title"), result.get("text") or "", link.get("anchor_text") or ""
        )
        if resolved != result.get("title"):
            result = dict(result)
            result["title"] = resolved
        try:
            built = build_candidate(
                result,
                candidate_id=item_id,
                observed_at=now,
                discovery_method="SEARCH",
                query_ref="index:" + link["index_url"],
            )
        except ValueError as exc:
            record["failures"].append({"stage": "NORMALIZE", "url": url, "reason_code": str(exc)[:120]})
            continue
        text = result.get("text") or ""
        record["items"].append(
            {
                "item_id": item_id,
                "source_id": source.id,
                "source_name": source.name,
                "domain": source.domain,
                "declared_language": source.language,
                "title": result.get("title"),
                "title_source": title_source,
                "text": text,
                "canonical_url": built["candidate"]["canonical_url"],
                "content_digest": built["candidate"]["content"]["digest"],
                "detected_language": built["language"]["tag"],
                "published_at": built["candidate"]["published_at"],
                # 配图一路留着：艺术/设计类内容光看文字判断不了
                "images": extract_images(fetched["html"] or "", result["final_url"]),
                "provenance": {
                    "index_url": link["index_url"],
                    "index_fetched_at": link["index_fetched_at"],
                    "list_position": link["position"],
                    "anchor_text": link["anchor_text"],
                    "matched_rule": link["path"],
                    "requested_url": result["requested_url"],
                    "final_url": result["final_url"],
                    "article_fetched_at": result["fetched_at"],
                    "http_status": result["status_code"],
                    "content_digest": built["candidate"]["content"]["digest"],
                    "raw_ref": str(raw_path).replace(chr(92), "/"),
                },
                "candidate": built["candidate"],
            }
        )
    _denoise(record, out)
    return record


def _denoise(record: dict[str, Any], out: Path) -> None:
    """同源多页去导航：一页里反复出现的行是页面框架，不是内容。

    原始正文照样留在 text 字段里（取证要看原文），削过的放在 text_clean
    （测量要用干净文本）。两个都留，是因为它们服务两个不同的目的。
    """
    items = record["items"]
    if not items:
        record["chrome"] = {"lines": 0, "shrink": 0.0}
        return
    pages = [item["text"] for item in items]
    outcome = denoise_apply(pages)
    for item, cleaned, shrink in zip(items, outcome["cleaned"], outcome["shrink"]):
        item["text_clean"] = cleaned
        item["chrome_removed_ratio"] = shrink
    chrome_dir = out / "chrome"
    chrome_dir.mkdir(parents=True, exist_ok=True)
    (chrome_dir / (record["source_id"] + ".json")).write_text(
        json.dumps(
            {"source_id": record["source_id"], "pages": len(pages), "lines": outcome["chrome"]},
            ensure_ascii=False,
            indent=2,
        )
        + chr(10),
        encoding="utf-8",
    )
    shrinks = [value for value in outcome["shrink"] if isinstance(value, float)]
    record["chrome"] = {
        "lines": len(outcome["chrome"]),
        "shrink_max": max(shrinks) if shrinks else 0.0,
        "shrink_min": min(shrinks) if shrinks else 0.0,
    }


def derive(directory: Path) -> int:
    """不联网，对已有 items.json 重算派生字段（目前是去导航后的正文）。

    存在的理由：正文预处理规则变了，不应该逼着重抓一遍——重抓既慢，
    又可能因为列表页更新而换掉一批内容，让前后对比失真。
    """
    items_path = directory / "items.json"
    if not items_path.exists():
        print("找不到 items.json:", items_path, file=sys.stderr)
        return 2
    items = json.loads(items_path.read_text(encoding="utf-8-sig"))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["source_id"], []).append(item)
    for source_id, group in grouped.items():
        record = {"source_id": source_id, "items": group}
        _denoise(record, directory)
        print(f"{source_id:20s} items={len(group):2d} chrome_lines={record['chrome']['lines']:3d} "
              f"shrink={record['chrome']['shrink_min']:.2f}-{record['chrome']['shrink_max']:.2f}")
    items_path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    print("已重写", items_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="按来源登记表抓内容（链接发现，可溯源）")
    parser.add_argument("--config", default=None, help="默认 config/user.yaml")
    parser.add_argument("--sources", default=None, help="默认读配置 sources.index")
    parser.add_argument("--out", default=None, help="输出目录，默认 runs/crawl-<时间戳>")
    parser.add_argument("--per-source", type=int, default=None, help="覆盖每个来源的条数上限")
    parser.add_argument("--only", default=None, help="只抓指定来源 id，逗号分隔")
    parser.add_argument("--field", default=None, help="只抓指定领域（分类法 field），逗号分隔")
    parser.add_argument("--role", default=None, help="只抓指定角色（core/task/explore/control），逗号分隔")
    parser.add_argument("--max-total", type=int, default=None, help="全局条数上限；默认读配置")
    parser.add_argument("--embed", action="store_true", default=None,
                        help="启用跨语言向量判定（默认读配置）")
    parser.add_argument("--no-embed", dest="embed", action="store_false",
                        help="强制关闭跨语言向量判定")
    parser.add_argument(
        "--proxy",
        default=None,
        help="HTTP 代理，例如 http://127.0.0.1:7897；不给则读 LAYA_PROXY 或配置",
    )
    parser.add_argument("--derive-only", default=None, help="只对已有抓取目录重算派生字段（不联网）")
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=None,
        help="整体抓取预算（秒）；默认读配置。到点后剩余来源记 SKIPPED_DEADLINE",
    )
    args = parser.parse_args(argv)

    config = user_config.load(args.config)
    if args.derive_only:
        return derive(Path(args.derive_only))

    sources_file = args.sources or str(user_config.get(config, "sources.index"))
    only = args.only
    if only is None:
        only = ",".join(user_config.get(config, "sources.only") or []) or None
    max_seconds = (int(user_config.get(config, "crawl.max_seconds", 1500))
                   if args.max_seconds is None else args.max_seconds)
    max_total = (int(user_config.get(config, "crawl.max_total", 0))
                 if args.max_total is None else args.max_total) or None
    embed = (bool(user_config.get(config, "crawl.embed", True))
             if args.embed is None else args.embed)
    proxy = args.proxy or os.environ.get("LAYA_PROXY") or str(
        user_config.get(config, "crawl.proxy")
        or user_config.get(config, "proxy.url")
        or ""
    ) or None
    if proxy and not proxy_alive(proxy):
        print("警告：代理 {} 连不上，按未配置处理（走代理的源会记 NEEDS_PROXY）".format(proxy), file=sys.stderr)
        proxy = None
    data = load(ROOT / sources_file)
    sources = data["sources"]
    if only:
        wanted = {token.strip() for token in only.split(",")}
        sources = [source for source in sources if source.id in wanted]
        if not sources:
            print("没有匹配的来源 id", file=sys.stderr)
            return 2
    # 33 个来源全抓一遍是几百次请求。日常运行按分类选子集，而不是全量扫。
    if args.field:
        wanted = {token.strip() for token in args.field.split(",")}
        sources = [source for source in sources if source.field in wanted]
    if args.role:
        wanted = {token.strip() for token in args.role.split(",")}
        sources = [source for source in sources if source.role in wanted]
    if not sources:
        print("过滤后没有来源可抓", file=sys.stderr)
        return 2

    # 设计/艺术源排最前：它们最容易被"外网超时吃光预算"挤掉，
    # 而它们恰恰是这个系统里最难替代的那一类内容。
    priority_fields = {"design", "art", "architecture"}
    sources = sorted(sources, key=lambda item: 0 if item.field in priority_fields else 1)

    now = datetime.now(timezone.utc)
    out = Path(args.out) if args.out else ROOT / "runs" / ("crawl-" + now.strftime("%Y%m%dT%H%M%SZ"))
    out.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    records = []
    all_items: list[dict[str, Any]] = []
    deadline = time.time() + max_seconds
    for source in sources:
        if time.time() > deadline:
            # 网络整体不可用时，逐个源等超时会把一次日常运行拖到一小时以上。
            # 到点就停，剩下的如实记成"没抓"，而不是假装抓过。
            records.append({
                "source_id": source.id,
                "source_name": source.name,
                "domain": source.domain,
                "status": source.status,
                "index_pages": [],
                "items": [],
                "failures": [
                    {"stage": "CRAWL", "url": source.pages[0].url if source.pages else "", "reason_code": "SKIPPED_DEADLINE"}
                ],
                "skipped_duplicates": 0,
            })
            print("{:20s} SKIPPED_DEADLINE".format(source.id))
            continue
        budget = args.per_source
        if max_total is not None:
            remaining = max_total - len(all_items)
            if remaining <= 0:
                break
            budget = min(budget if budget is not None else source.per_source, remaining)
        record = crawl_source(source, out=out, now=now, per_source=budget, seen_urls=seen, proxy=proxy)
        records.append(record)
        all_items.extend(record["items"])
        print(
            "{:20s} {:12s} items={:2d} fail={:2d} dup={:2d}".format(
                source.id, source.domain, len(record["items"]), len(record["failures"]), record["skipped_duplicates"]
            )
        )

    items = [{key: value for key, value in item.items() if key != "candidate"} for item in all_items]
    # 事件聚簇要在全部来源抓完之后做：它比的是"不同来源说的是不是同一件事"。
    vectors = None
    if embed:
        if embeddings_available():
            vectors = embed_items(items)
            print("已算 {} 条向量，用于跨语言判同".format(len(vectors)))
        else:
            print("没有本地多语言模型，跳过向量判定")
    event_map = cluster(items, embeddings=vectors)
    for item in items:
        item.update(event_map.get(item["item_id"], {}))
    candidates = [item["candidate"] for item in all_items]

    (out / "discovered.json").write_text(
        json.dumps({"generated_at": now.isoformat(timespec="seconds"), "sources": records}, ensure_ascii=False, indent=2)
        + chr(10),
        encoding="utf-8",
    )
    (out / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    (out / "candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    with (out / "provenance.jsonl").open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(
                json.dumps(
                    {
                        "item_id": item["item_id"],
                        "source_id": item["source_id"],
                        "title": item["title"],
                        **item["provenance"],
                    },
                    ensure_ascii=False,
                )
                + chr(10)
            )

    by_domain: dict[str, int] = {}
    for item in items:
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1

    manifest = {
        "crawler": "tools/crawl.py",
        "started_at": now.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources_file": sources_file,
        "user_agent": data["defaults"]["user_agent"],
        "totals": {
            "items": len(items),
            "sources": len(records),
            "failures": sum(len(record["failures"]) for record in records),
            "duplicates": sum(record["skipped_duplicates"] for record in records),
            "by_domain": by_domain,
            "event_clusters": sum(1 for item in items if item.get("event_size", 1) > 1 and item.get("event_role") == "REPRESENTATIVE"),
            "event_members": sum(1 for item in items if item.get("event_role") == "MEMBER"),
        },
        "sources": [
            {
                "source_id": record["source_id"],
                "name": record.get("name") or record.get("source_name") or record["source_id"],
                "domain": record["domain"],
                "items": len(record["items"]),
                "failures": record["failures"],
                "index_pages": record["index_pages"],
            }
            for record in records
        ],
        "contract_version": "1.0",
    }
    (out / "crawl-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")

    (out / "README.md").write_text(
        "# 本次抓取的取证链" + chr(10) + chr(10)
        + "任何一条内容都可以按下面的顺序回溯：" + chr(10) + chr(10)
        + "1. provenance.jsonl —— 按 item_id 找到该条：列表页地址、列表抓取时间、列表中的位置、" + chr(10)
        + "   匹配到的路径、文章最终地址、正文抓取时间、HTTP 状态、内容摘要。" + chr(10)
        + "2. discovered.json —— 该列表页当时用了哪条正则、共抽到多少链接、为什么挑中这些。" + chr(10)
        + "3. raw/<item_id>.html —— 抓下来的原始 HTML，可重新抽正文复核摘要是否一致。" + chr(10)
        + "4. crawl-manifest.json —— 本次抓取的时间、来源清单、失败原因汇总。" + chr(10),
        encoding="utf-8",
    )

    print(
        chr(10) + "合计 " + str(len(items)) + " 条 · 来源 " + str(len(records)) + " 个 · 失败 "
        + str(manifest["totals"]["failures"]) + " · 去重 " + str(manifest["totals"]["duplicates"])
    )
    print("按领域:", json.dumps(by_domain, ensure_ascii=False))
    print("事件簇: {} 组，成员 {} 条".format(manifest["totals"]["event_clusters"], manifest["totals"]["event_members"]))
    print("输出目录:", out)
    return 0 if items else 1


if __name__ == "__main__":
    raise SystemExit(main())

