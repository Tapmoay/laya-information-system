"""极简 HTML 抽取（stdlib，无第三方依赖）。

只取确定性字段：标题、canonical、语言、发布时间、作者、站点名、正文文本。
不解析 JS、不执行脚本、不做可读性打分——这些都会让"同一份 HTML 得到不同结果"。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

WHITESPACE = re.compile(r"[ \t\r\f\v]+")
SKIP_TAGS = {"script", "style", "noscript", "template"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.lang: str | None = None
        self.text_parts: list[str] = []
        self._in_title = False
        self._in_h1 = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag == "html" and attributes.get("lang"):
            self.lang = attributes["lang"]
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
        elif tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content")
            if key and content:
                self.meta[key] = content
        elif tag == "link" and (attributes.get("rel") or "").lower() == "canonical":
            self.canonical = attributes.get("href") or None
        elif tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3", "blockquote"} and not self._skip_depth:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif self._in_h1 and not self._skip_depth:
            self.h1_parts.append(data)
        elif not self._skip_depth:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped + " ")


def _clean(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


def _prefer_heading(title: str | None, heading: str | None) -> str | None:
    """栏目名塞在标题里的站点，用 <h1> 当标题。

    Hugging Face 的论文页是 "Paper page - 真正的标题"，站点名和标题被拼在一起。
    判据写死成三条，宁可不动也不要猜：<h1> 是标题的子串、<h1> 比标题短、
    <h1> 至少占标题六成长度。三条都满足才替换——少一条就保持原样。
    """
    if not title or not heading:
        return title
    if heading == title or heading not in title:
        return title
    if len(heading) < 12 or len(heading) < len(title) * 0.6:
        return title
    return heading


JSONLD = re.compile(r'"datePublished"\s*:\s*"([^"]{4,40})"', re.I)
TIME_TAG = re.compile(r"<time[^>]*datetime=[\"']([^\"']{4,40})[\"']", re.I)


def _published_at(parser: "_Extractor", html: str) -> str | None:
    """按可靠性从高到低取发布时间。

    顺序是有理由的，不是随便排的：
      1. meta 里明写的发布时间——站点主动声明的机器可读字段
      2. JSON-LD 的 datePublished——结构化数据，多数 CMS 会输出
      3. <time datetime>——语义化标记
    这三条都是"页面自己说的"，不是我们猜的。都取不到就返回 None，
    由下游记 UNKNOWN；用抓取时间顶替是被明令禁止的。
    """
    for key in ("article:published_time", "datepublished", "date", "og:published_time"):
        value = parser.meta.get(key)
        if value and value.strip():
            return value.strip()
    match = JSONLD.search(html)
    if match:
        return match.group(1).strip()
    match = TIME_TAG.search(html)
    if match:
        return match.group(1).strip()
    return None


def extract(html: str) -> dict:
    parser = _Extractor()
    parser.feed(html)
    parser.close()

    title = parser.meta.get("og:title") or _clean("".join(parser.title_parts)) or None
    title = _prefer_heading(title, _clean("".join(parser.h1_parts)) or None)
    description = parser.meta.get("og:description") or parser.meta.get("description")
    body = _clean(" ".join(parser.text_parts))
    body = re.sub(r"\s*\n\s*", "\n", body).strip()

    return {
        "title": title,
        "description": description,
        "canonical_link": parser.canonical,
        "declared_language": parser.lang,
        "site_name": parser.meta.get("og:site_name"),
        "author_name": parser.meta.get("author") or parser.meta.get("article:author"),
        "published_at_raw": _published_at(parser, html),
        "text": body,
    }

_HEADER_LINE = re.compile(
    r"^\s*(?:日期|作者|预计阅读时间|阅读时间|发布时间|更新时间|来源|标签|字数)\s*[：:]"
)


def derive_title(text: str, limit: int = 60) -> str | None:
    """没有 og:title / <title> / h1 时，从正文第一句推一个标题。

    为什么要有它：缺标题的页面不代表没有内容。整条丢掉，等于因为解析失败
    把一个可能有价值的条目判死。推出来的标题是**正文的一部分**，不是编造；
    但它比真标题弱，所以调用方要记下来源（见 tools/crawl.py 的 title_source）。
    """
    body = (text or "").strip()
    if not body:
        return None
    for line in body.splitlines():
        line = line.strip()
        if len(line) < 8 or _HEADER_LINE.match(line):
            continue
        for cut in ("。", "！", "？", ".", "!", "?"):
            head = line.split(cut)[0].strip()
            if 8 <= len(head) <= limit:
                return head
        return line[: limit - 1] + "…" if len(line) > limit else line
    return None

