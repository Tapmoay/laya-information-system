"""来源登记表的读取与校验（信息源库 v2）。

登记表是"我们会去哪些地方找东西"的唯一事实来源。v2 加的是分类法：
一个来源不再只有一个 domain 字段，而是同时带上

    field  内容讲哪个领域        （ai / security / engineering / ...）
    kind   内容是怎么产出的      （preprint / aggregator / lab_release / ...）
    role   它服务用户的哪个目标  （core / task / explore / control）
    serves 它具体喂哪个目标或任务

为什么要分类而不是继续加 domain：domain 只能回答"这是什么主题"，
回答不了"这个源凭什么值得每天抓"。后者由 kind（决定信息密度与时效）
和 role（决定它进核心位还是探索位）共同决定。分类法写在这里而不是散在
文档里，是为了让"加一个源"这个动作必须当场回答这两个问题，答不上来就报错。

登记表必须能在没有网络时被校验，否则一个源失效要等到抓取时才暴露。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ingest.discover import LinkRule

DEFAULT_SOURCE = {
    "timeout_seconds": 20,
    "max_attempts": 2,
    "max_bytes": 3_000_000,
    "respect_robots": True,
    "user_agent": "LAYA-personal-information/0.1",
}

FACETS = ("field", "kind", "role", "language", "density")
REQUIRED = ("field", "kind", "role", "language")
STATUSES = ("VERIFIED", "UNSTABLE", "UNVERIFIED", "BLOCKED")
# 出口：direct 能直连，proxy 必须走代理（直连被 TLS 阻断）。
# 把它写成数据而不是记在脑子里：不然每次换机器都要重新踩一遍同样的坑。
NETWORKS = ("direct", "proxy")


class SourceIndexError(ValueError):
    pass


@dataclass(frozen=True)
class IndexPage:
    url: str
    rule: LinkRule


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    field: str
    kind: str
    role: str
    language: str
    density: str
    network: str
    status: str
    why: str
    serves: tuple[str, ...]
    per_source: int
    pages: tuple[IndexPage, ...]
    options: dict[str, Any]

    @property
    def domain(self) -> str:
        """抓取记录里的 domain 就是分类法里的 field，保留旧名字免得下游全改。"""
        return self.field


def _rule(data: dict[str, Any], per_source: int) -> LinkRule:
    pattern = data.get("pattern")
    if not pattern:
        raise SourceIndexError("列表页缺少 pattern")
    # 反斜杠在 YAML、JS 字符串、复制粘贴里最容易掉。掉了之后正则**依然能编译**，
    # 只是永远匹配不上——于是这个源安静地一条都抓不到，日志里一切正常。
    # 实测踩过一次（三个新源的 \d 全变成了 d），所以在这里直接拦。
    if re.search(r"(?<!\\)d\{", str(pattern)):
        raise SourceIndexError(f"路径正则里出现 d{{...}}，疑似丢了反斜杠：{pattern}")
    return LinkRule(
        pattern=str(pattern),
        min_anchor_chars=int(data.get("min_anchor_chars", 8)),
        limit=int(data.get("limit", per_source)),
        exclude=tuple(data.get("exclude") or ()),
    )


def _facet(taxonomy: dict[str, Any], facet: str, value: Any, source_id: str) -> str:
    allowed = taxonomy.get(facet) or {}
    text = str(value or "").strip()
    if not text:
        raise SourceIndexError(f"{source_id} 缺少 {facet}（分类法必填）")
    if allowed and text not in allowed:
        raise SourceIndexError(f"{source_id} 的 {facet}={text} 不在分类法里：{sorted(allowed)}")
    return text


def load(path: Path | str) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = {**DEFAULT_SOURCE, **(data.get("defaults") or {})}
    taxonomy = data.get("taxonomy") or {}
    targets = data.get("targets") or {}
    target_ids = {str(item.get("id")) for group in targets.values() for item in (group or [])}
    raw_sources = data.get("sources") or []
    if not raw_sources:
        raise SourceIndexError("来源登记表为空")

    sources: list[Source] = []
    seen: set[str] = set()
    for entry in raw_sources:
        source_id = str(entry.get("id") or "").strip()
        if not source_id:
            raise SourceIndexError("来源缺少 id")
        if source_id in seen:
            raise SourceIndexError(f"来源 id 重复：{source_id}")
        seen.add(source_id)

        status = str(entry.get("status") or "UNVERIFIED")
        if status not in STATUSES:
            raise SourceIndexError(f"{source_id} 的 status={status} 不在 {STATUSES}")

        facets = {name: _facet(taxonomy, name, entry.get(name), source_id) for name in REQUIRED if name != "language"}
        # language 不放进分类法，只允许 zh-Hans / en 两种，免得出现 zh / zh-cn / zh_CN 三种写法
        language = str(entry.get("language") or "").strip()
        if language not in ("zh-Hans", "en"):
            raise SourceIndexError(f"{source_id} 的 language 必须是 zh-Hans 或 en，拿到 {language!r}")
        facets["language"] = language
        density = str(entry.get("density") or "").strip()
        if density and density not in (taxonomy.get("density") or {}):
            raise SourceIndexError(f"{source_id} 的 density={density} 不在分类法里")
        network = str(entry.get("network") or "direct").strip()
        if network not in NETWORKS:
            raise SourceIndexError(f"{source_id} 的 network={network} 不在 {NETWORKS}")

        serves = tuple(str(item) for item in (entry.get("serves") or ()))
        if not serves:
            raise SourceIndexError(f"{source_id} 缺少 serves：说不出它喂哪个目标，就不该被每天抓")
        unknown = [item for item in serves if target_ids and item not in target_ids]
        if unknown:
            raise SourceIndexError(f"{source_id} 的 serves 引用了未登记目标：{unknown}")

        pages_raw = entry.get("indexes") or []
        if not pages_raw:
            raise SourceIndexError(f"{source_id} 缺少 indexes")
        per_source = int(entry.get("per_source", 8))
        pages = tuple(
            IndexPage(url=str(page["url"]), rule=_rule(page, per_source)) for page in pages_raw
        )
        for page in pages:  # 规则写错要当场炸
            re.compile(page.rule.pattern)
        options = {key: entry.get(key, defaults[key]) for key in DEFAULT_SOURCE}
        sources.append(
            Source(
                id=source_id,
                name=str(entry.get("name") or source_id),
                field=facets["field"],
                kind=facets["kind"],
                role=facets["role"],
                language=language,
                density=density,
                network=network,
                status=status,
                why=str(entry.get("why") or ""),
                serves=serves,
                per_source=per_source,
                pages=pages,
                options=options,
            )
        )
    return {"defaults": defaults, "taxonomy": taxonomy, "targets": targets, "sources": sources}


def hosts(sources: list[Source]) -> list[str]:
    """登记表里出现过的全部主机——抓取白名单由它生成，而不是手写。"""
    from urllib.parse import urlsplit

    found: list[str] = []
    for source in sources:
        for page in source.pages:
            host = (urlsplit(page.url).hostname or "").lower()
            if host and host not in found:
                found.append(host)
    return found
