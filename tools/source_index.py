"""把来源登记表渲染成人能读的索引，并导出机器能查的索引文件。

    python tools/source_index.py --render      # 生成 docs/26-source-library.md + config/sources.index.json
    python tools/source_index.py --check       # 只校验分类法与引用完整性
    python tools/source_index.py --query field=ai --query role=core
    python tools/source_index.py --hosts       # 打印抓取白名单

为什么要生成而不是手写：手写的清单一定会和登记表漂移，而"文档说 18 个源、
登记表里 21 个"这种错误没有任何人会发现。生成之后只有一处事实。
判断结论（哪个源符合要求、哪个要降级）不在这里，在 docs/27-source-audit.md——
那是人的判断，不该被机器覆盖。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest.source_index import Source, hosts as source_hosts, load  # noqa: E402

BT = chr(96)
REGISTRY = ROOT / "config" / "sources-index.yaml"
DOC = ROOT / "docs" / "26-source-library.md"
INDEX_JSON = ROOT / "config" / "sources.index.json"


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _code(text: str) -> str:
    return BT + str(text) + BT


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_cell(item) for item in row) + " |")
    return out


def _group(sources: list[Source], facet: str) -> dict[str, list[Source]]:
    grouped: dict[str, list[Source]] = {}
    for source in sources:
        grouped.setdefault(getattr(source, facet), []).append(source)
    return dict(sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])))


def render(data: dict[str, Any]) -> str:
    sources: list[Source] = data["sources"]
    taxonomy: dict[str, Any] = data["taxonomy"]
    targets: dict[str, Any] = data["targets"]
    goals = targets.get("goals") or []
    tasks = targets.get("tasks") or []
    lines: list[str] = []

    lines.append("# 26 · 信息源库：分类与索引")
    lines.append("")
    lines.append(
        "> 本文件由 " + _code("python tools/source_index.py --render") + " 生成，不要手改。"
    )
    lines.append(
        "> 分类法与来源定义在 " + _code("config/sources-index.yaml") + "；"
        "哪个源符合要求、哪个要降级，是人的判断，写在 " + _code("docs/27-source-audit.md") + "。"
    )
    lines.append("")

    # ── 1. 规模 ─────────────────────────────────────────────
    lines.append("## 1. 规模")
    lines.append("")
    fields = _group(sources, "field")
    kinds = _group(sources, "kind")
    roles = _group(sources, "role")
    verified = sum(1 for item in sources if item.status == "VERIFIED")
    lines.append(
        "共 **"
        + str(len(sources))
        + "** 个来源，覆盖 **"
        + str(len(fields))
        + "** 个领域、**"
        + str(len(kinds))
        + "** 种来源形态；状态 " + _code("VERIFIED") + " 的 "
        + str(verified)
        + " 个。"
    )
    lines.append("")
    lines.append(
        "分类维度不是装饰："
        + _code("field")
        + " 决定它是什么主题，"
        + _code("kind")
        + " 决定它的密度和时效长什么样，"
        + _code("role")
        + " 决定它进核心位还是探索位。"
        "一个源说不出这三件事，就不该被每天抓。"
    )
    lines.append("")

    # ── 2. 分类法 ───────────────────────────────────────────
    lines.append("## 2. 分类法")
    lines.append("")
    for facet, values in taxonomy.items():
        grouped = _group(sources, facet) if facet in ("field", "kind", "role", "language", "density") else {}
        lines.append("### 2." + str(list(taxonomy).index(facet) + 1) + " " + _code(facet))
        lines.append("")
        rows = []
        for key, meaning in values.items():
            count = len(grouped.get(key, []))
            rows.append([_code(key), meaning, count])
        lines.extend(_table(["取值", "含义", "来源数"], rows))
        lines.append("")

    # ── 3. 覆盖矩阵 ─────────────────────────────────────────
    lines.append("## 3. 覆盖：每个目标靠哪些源喂")
    lines.append("")
    lines.append("判断一个源该不该留的第一个问题不是它好不好，而是**没有它哪块会空**。")
    lines.append("")
    rows = []
    for group, label in ((goals, "长期目标"), (tasks, "任务候选")):
        for item in group:
            target_id = str(item.get("id"))
            feeders = [source for source in sources if target_id in source.serves]
            langs = sorted({source.language for source in feeders})
            rows.append(
                [
                    label,
                    item.get("tier", "-"),
                    _code(target_id),
                    item.get("statement", ""),
                    len(feeders),
                    "、".join(langs) if langs else "**空**",
                ]
            )
    lines.extend(_table(["类别", "层级", "目标", "说明", "来源数", "语言"], rows))
    lines.append("")

    # ── 4. 按领域索引 ───────────────────────────────────────
    lines.append("## 4. 按领域索引")
    lines.append("")
    for field_name, group in fields.items():
        meaning = (taxonomy.get("field") or {}).get(field_name, "")
        lines.append("### " + field_name + ("（" + meaning + "）" if meaning else ""))
        lines.append("")
        rows = []
        for source in sorted(group, key=lambda item: (item.kind, item.id)):
            rows.append(
                [
                    _code(source.id),
                    source.name,
                    source.kind,
                    source.role,
                    source.language,
                    source.density or "-",
                    source.network,
                    source.status,
                    ", ".join(source.serves),
                ]
            )
        lines.extend(
            _table(["id", "名称", "kind", "role", "语言", "密度", "出口", "状态", "serves"], rows)
        )
        lines.append("")

    # ── 5. 全部来源 ─────────────────────────────────────────
    lines.append("## 5. 全部来源与它为什么在库里")
    lines.append("")
    for source in sorted(sources, key=lambda item: item.id):
        urls = "、".join(_code(page.url) for page in source.pages)
        lines.append("#### " + _code(source.id) + " " + source.name)
        lines.append("")
        lines.append(
            "- 分类："
            + " / ".join([source.field, source.kind, source.role])
            + "；语言 " + source.language
            + "；出口 " + source.network
            + "；每源预算 " + str(source.per_source)
        )
        lines.append("- 列表页：" + urls)
        for page in source.pages:
            lines.append(
                "  - 路径正则 " + _code(page.rule.pattern)
                + "，锚文本至少 " + str(page.rule.min_anchor_chars) + " 字"
            )
        lines.append("- 为什么在库里：" + (source.why or "（未写）"))
        lines.append("")

    # ── 6. 索引用法 ─────────────────────────────────────────
    lines.append("## 6. 索引怎么用")
    lines.append("")
    lines.append("机器可查的索引在 " + _code("config/sources.index.json") + "，字段与登记表一一对应；"
                 "按条件筛选：")
    lines.append("")
    lines.append(BT * 3 + "powershell")
    lines.append("python tools/source_index.py --query field=ai --query role=core")
    lines.append("python tools/source_index.py --query kind=preprint")
    lines.append("python tools/source_index.py --hosts")
    lines.append("python tools/source_index.py --verify          # 联网逐个列表页实测（有网络时才跑）")
    lines.append("python tools/source_index.py --verify --proxy http://127.0.0.1:7897")
    lines.append("")
    lines.append(
        "出口为 " + _code("proxy") + " 的来源直连会被 TLS 阻断（实测 projectzero.google、"
        "huggingface.co）。代理不写进登记表的具体地址——那是机器属性，"
        "用 " + _code("--proxy") + " 或环境变量 " + _code("LAYA_PROXY") + " 给。"
    )
    lines.append(BT * 3)
    lines.append("")
    lines.append("加一个源必须先回答三件事：")
    lines.append("")
    lines.append("1. " + _code("field") + " 它讲哪个领域（决定会不会挤占别的领域的名额）")
    lines.append("2. " + _code("kind") + " 它是怎么产出的（预印本/一手公告/媒体编译，密度差一个量级）")
    lines.append("3. " + _code("serves") + " 它喂哪个目标：说不出喂哪个目标，就说明它只是看着像好东西")
    lines.append("")
    lines.append("任何一项缺失，" + _code("--check") + " 都会报错，不允许悄悄进库。")
    lines.append("")
    return "\n".join(lines) + "\n"


def machine_index(data: dict[str, Any]) -> dict[str, Any]:
    sources: list[Source] = data["sources"]
    return {
        "schema": 2,
        "registry": "config/sources-index.yaml",
        "taxonomy": data["taxonomy"],
        "targets": data["targets"],
        "counts": {
            "sources": len(sources),
            "fields": len({item.field for item in sources}),
            "kinds": len({item.kind for item in sources}),
            "verified": sum(1 for item in sources if item.status == "VERIFIED"),
            "needs_proxy": sum(1 for item in sources if item.network == "proxy"),
        },
        "sources": [
            {
                "id": item.id,
                "name": item.name,
                "field": item.field,
                "kind": item.kind,
                "role": item.role,
                "language": item.language,
                "density": item.density or None,
                "network": item.network,
                "status": item.status,
                "serves": list(item.serves),
                "per_source": item.per_source,
                "why": item.why,
                "indexes": [
                    {
                        "url": page.url,
                        "pattern": page.rule.pattern,
                        "min_anchor_chars": page.rule.min_anchor_chars,
                        "exclude": list(page.rule.exclude),
                    }
                    for page in item.pages
                ],
            }
            for item in sorted(sources, key=lambda source: source.id)
        ],
    }


def query(sources: list[Source], conditions: list[str]) -> list[Source]:
    pairs: list[tuple[str, str]] = []
    for condition in conditions:
        if "=" not in condition:
            raise SystemExit("查询条件要写成 k=v：" + condition)
        key, value = condition.split("=", 1)
        pairs.append((key.strip(), value.strip()))
    matched = []
    for source in sources:
        ok = True
        for key, value in pairs:
            if key == "serves":
                ok = value in source.serves
            else:
                ok = str(getattr(source, key, "")) == value
            if not ok:
                break
        if ok:
            matched.append(source)
    return matched


def verify(sources: list[Source], *, sleep: float = 1.0, proxy: str | None = None) -> int:
    """联网验一遍：每个列表页到底能抽出多少条。

    这个动作必须联网，所以不放进 tools/check.py（那套检查要能离线跑）。
    它的用途是"加完一个源、改完一条正则之后立刻验一次"，而不是每天跑。
    只验发现阶段，不抓正文——正文能不能抓，要看一次真实抓取。
    """
    import time
    from datetime import datetime, timezone

    from ingest.discover import discover
    from ingest.fetch import FetchPolicy, fetch_page
    from ingest.urls import host_of

    now = datetime.now(timezone.utc)
    bad = 0
    for source in sources:
        if source.network == "proxy" and not proxy:
            print(source.id.ljust(22) + "NEEDS_PROXY（该源直连被阻断，请加 --proxy）")
            continue
        for page in source.pages:
            host = host_of(page.url)
            bare = ".".join(host.split(".")[-2:]) if host.count(".") >= 2 else host
            policy = FetchPolicy(
                allowed_hosts=(host, bare),
                timeout_seconds=float(source.options["timeout_seconds"]),
                max_attempts=1,
                max_bytes=int(source.options["max_bytes"]),
                respect_robots=bool(source.options["respect_robots"]),
                user_agent=str(source.options["user_agent"]),
                proxy=proxy,
            )
            label = source.id.ljust(22) + page.url.ljust(46)
            try:
                fetched = fetch_page(page.url, request_id="verify-" + source.id, policy=policy, now=now)
            except Exception as exc:  # noqa: BLE001
                print(label + "EXC " + type(exc).__name__)
                bad += 1
                continue
            result = fetched["result"]
            html = fetched["html"] or ""
            if not html:
                print(label + "FAIL " + str(result.get("error")))
                bad += 1
                continue
            found = discover(page.url, html, page.rule)
            counts = found["counts"]
            if not counts["selected"]:
                bad += 1
            print(
                label
                + ("OK   " if counts["selected"] else "EMPTY")
                + " anchors=" + str(counts["anchors"]).rjust(4)
                + " selected=" + str(counts["selected"]).rjust(3)
            )
            for link in found["links"][:2]:
                print("".ljust(24) + "  - " + link["path"] + "  | " + link["anchor_text"][:50])
            time.sleep(sleep)
    print("")
    print("有问题的列表页：" + str(bad))
    return 0 if bad == 0 else 1


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="信息源库：渲染索引 / 校验 / 查询")
    parser.add_argument("--render", action="store_true", help="生成 docs/26 与 config/sources.index.json")
    parser.add_argument("--check", action="store_true", help="只校验登记表")
    parser.add_argument("--query", action="append", default=[], metavar="K=V", help="按分类筛选")
    parser.add_argument("--hosts", action="store_true", help="打印抓取白名单")
    parser.add_argument("--verify", action="store_true", help="联网验一遍每个列表页能抽出多少条")
    parser.add_argument("--only", default=None, help="只验指定来源 id，逗号分隔")
    parser.add_argument("--proxy", default=None, help="HTTP 代理；不给则读 LAYA_PROXY")
    args = parser.parse_args(argv)

    try:
        data = load(REGISTRY)
    except Exception as exc:  # noqa: BLE001
        print("登记表不合法：" + str(exc))
        return 1
    sources: list[Source] = data["sources"]

    if args.query:
        for source in query(sources, args.query):
            print(
                " / ".join([source.id, source.name, source.field, source.kind, source.role, source.status])
            )
        return 0

    if args.hosts:
        for host in source_hosts(sources):
            print(host)
        return 0

    if args.check:
        print("来源 " + str(len(sources)) + " 个，分类法与 serves 引用均合法。")
        return 0

    if args.verify:
        if args.only:
            wanted = {token.strip() for token in args.only.split(",")}
            sources = [source for source in sources if source.id in wanted]
            if not sources:
                print("没有匹配的来源 id")
                return 2
        return verify(sources, proxy=args.proxy or os.environ.get("LAYA_PROXY") or None)

    if args.render:
        DOC.write_text(render(data), encoding="utf-8")
        INDEX_JSON.write_text(
            json.dumps(machine_index(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("已写 " + str(DOC.relative_to(ROOT)) + " 与 " + str(INDEX_JSON.relative_to(ROOT)))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
