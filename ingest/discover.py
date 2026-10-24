"""从索引／列表页发现文章链接（抓取的"发现"阶段）。

为什么需要它：RSS 只覆盖少数站点，而信息最不缺的就是网页。
但"能抓"不等于"可溯源"——所以要留下三样东西：
调用方给的路径正则（决定"为什么认为这是个链接"）、
文档顺序（决定"列表页当时的排序"）、以及索引页本身的抓取时间。

不做 JS 渲染、不做可读性打分：同一份 HTML 必须永远得到同一组链接。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from ingest.urls import canonicalize_url, host_of

ANCHOR_RE = re.compile(r"<a\s[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"[ \t\r\f\v]+")
ENTITY = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " "}


def _anchor_text(inner_html: str) -> str:
    text = TAG_RE.sub(" ", inner_html)
    for entity, replacement in ENTITY.items():
        text = text.replace(entity, replacement)
    return WHITESPACE.sub(" ", text).strip()


@dataclass(frozen=True)
class LinkRule:
    """挑链接的规则。正则匹配 path（含 ?query，不含主机名）。"""

    pattern: str
    min_anchor_chars: int = 8
    limit: int = 12
    exclude: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        re.compile(self.pattern)  # 规则写错要当场炸，不要等到抓完才发现


def extract_links(html: str, base_url: str) -> list[dict]:
    """按文档顺序抽出所有绝对化后的链接（不去重、不筛选）。"""
    links: list[dict] = []
    for order, (href, inner) in enumerate(ANCHOR_RE.findall(html)):
        href = href.strip()
        if not href or href.startswith("#"):
            continue
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme.lower() not in {"http", "https"}:
            continue
        links.append(
            {
                "order": order,
                "href": absolute,
                "host": (parts.hostname or "").lower(),
                # 路径与查询串分开存：绝大多数站点的"是不是文章"由路径决定，
                # 把 ?utm_source=... 拼进路径会让正则被迫跟着污染的路径走。
                "path": parts.path or "/",
                "query": parts.query,
                "anchor_text": _anchor_text(inner),
            }
        )
    return links


def select_links(
    links: list[dict],
    rule: LinkRule,
    *,
    host: str,
    base_url: str | None = None,
) -> list[dict]:
    """按规则挑链接：同主机 → 路径正则 → 锚文本长度 → 去重 → 截断。

    base_url 给定时，返回的 anchor 若是相对路径会被拼成绝对地址（部分站点锚文本是标题、
    链接在 data 属性里，这类站不适用本函数）。
    """
    pattern = re.compile(rule.pattern)
    seen: set[str] = set()
    chosen: list[dict] = []
    for link in links:
        if link["host"] != host:
            continue
        # 尾部斜杠是最常见的"看起来不一样"：/a/b 与 /a/b/ 是同一篇文章。
        normalized = link["path"].rstrip("/") or "/"
        if not pattern.search(normalized):
            continue
        if len(link["anchor_text"]) < rule.min_anchor_chars:
            continue
        if any(token in normalized for token in rule.exclude):
            continue
        if "\u00a0" in link["anchor_text"]:
            continue
        try:
            canonical = canonicalize_url(link["href"])
        except ValueError:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        chosen.append({**link, "canonical_url": canonical})
        if len(chosen) >= rule.limit:
            break
    return chosen


def discover(index_url: str, html: str, rule: LinkRule) -> dict:
    """从一页索引发现链接，并附上足够的溯源信息。"""
    host = host_of(index_url)
    links = extract_links(html, index_url)
    selected = select_links(links, rule, host=host)
    return {
        "index_url": index_url,
        "index_host": host,
        "rule": {
            "pattern": rule.pattern,
            "min_anchor_chars": rule.min_anchor_chars,
            "limit": rule.limit,
            "exclude": list(rule.exclude),
        },
        "counts": {"anchors": len(links), "same_host": sum(1 for l in links if l["host"] == host), "selected": len(selected)},
        "links": [
            {
                "url": item["href"],
                "canonical_url": item["canonical_url"],
                "path": item["path"],
                "query": item.get("query", ""),
                "anchor_text": item["anchor_text"],
                "position": item["order"],
            }
            for item in selected
        ],
    }


def listing_pages(index_url: str, html: str, *, pattern: str, limit: int = 5) -> list[str]:
    """找出同一列表的后续分页（列表页常见 /page/2、?page=2、_2.html）。

    只返回与索引页同主机、且路径能匹配给定模式的地址；不去猜分页参数，
    猜错会变成"悄悄少抓一页"，比明确抓不到更糟。
    """
    regex = re.compile(pattern)
    host = host_of(index_url)
    pages: list[str] = []
    for link in extract_links(html, index_url):
        if link["host"] != host:
            continue
        if not regex.search(link["path"]):
            continue
        if link["href"] in pages:
            continue
        pages.append(link["href"])
        if len(pages) >= limit:
            break
    return pages
