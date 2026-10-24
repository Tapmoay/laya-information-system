"""链接发现：路径正则、同主机、去重、编码嗅探、登记表校验。"""

from __future__ import annotations

import pytest

from ingest.charset import sniff_charset
from ingest.discover import LinkRule, discover, extract_links, select_links
from ingest.source_index import SourceIndexError, load

INDEX = """
<html><body>
<a href="/articles/101.html">够长的一个标题用来通过长度过滤</a>
<a href="/articles/102.html/">带尾斜杠的同一篇文章</a>
<a href="/articles/101.html?utm_source=x">同一篇的追踪链接</a>
<a href="/about">关于我们</a>
<a href="javascript:void(0)">点我</a>
<a href="https://other.example/articles/103.html">站外文章标题足够长了吧</a>
<a href="/articles/104.html">短</a>
</body></html>
"""


def _links(html=INDEX):
    return extract_links(html, "https://example.com/")


def test_extract_links_keeps_order_and_resolves_relative():
    links = _links()
    assert links[0]["href"] == "https://example.com/articles/101.html"
    assert links[0]["order"] == 0
    assert all(link["href"].startswith("https://") for link in links)
    assert not any(link["href"].startswith("javascript") for link in links)


def test_select_links_enforces_host_pattern_and_length():
    chosen = select_links(
        _links(), LinkRule(pattern=r"^/articles/\d+\.html$", min_anchor_chars=8, limit=10),
        host="example.com",
    )
    urls = [item["canonical_url"] for item in chosen]
    assert "https://example.com/articles/101.html" in urls
    assert all("other.example" not in url for url in urls)
    assert all(url.endswith(".html") for url in urls)


def test_trailing_slash_and_tracking_params_collapse_to_one_item():
    chosen = select_links(
        _links(), LinkRule(pattern=r"^/articles/\d+\.html$", min_anchor_chars=8, limit=10),
        host="example.com",
    )
    # 101 有三个写法（原样 / 尾斜杠 / utm），必须收敛成一条；102 是另一篇文章，不能合并
    assert len({item["canonical_url"] for item in chosen}) == len(chosen)
    assert len(chosen) == 2
    assert sum("101.html" in item["canonical_url"] for item in chosen) == 1


def test_exclude_blocks_paths():
    chosen = select_links(
        _links(), LinkRule(pattern=r"^/articles/\d+\.html$", min_anchor_chars=8, exclude=("101",)),
        host="example.com",
    )
    assert all("101" not in item["canonical_url"] for item in chosen)


def test_limit_is_respected():
    chosen = select_links(
        _links(), LinkRule(pattern=r"^/articles/", min_anchor_chars=1, limit=2), host="example.com"
    )
    assert len(chosen) == 2


def test_discover_echoes_rule_for_audit():
    found = discover("https://example.com/", INDEX, LinkRule(pattern=r"^/articles/\d+\.html$"))
    assert found["rule"]["pattern"] == r"^/articles/\d+\.html$"
    assert found["counts"]["anchors"] >= 5
    assert found["counts"]["selected"] == len(found["links"])


def test_bad_pattern_fails_loudly():
    with pytest.raises(Exception):
        LinkRule(pattern="(unclosed")


# ------------------------------------------------------------------ 编码


def test_charset_header_wins_and_gb_variants_upgrade():
    assert sniff_charset(b"", "gbk") == "gb18030"
    assert sniff_charset(b"", "gb2312") == "gb18030"
    assert sniff_charset(b"", "utf-8") == "utf-8"


def test_charset_sniffed_from_meta_when_header_silent():
    raw = "<html><head><meta charset=\"gbk\"></head>".encode("ascii")
    assert sniff_charset(raw, None) == "gb18030"


def test_charset_defaults_to_utf8():
    assert sniff_charset(b"<html><body>plain</body></html>", None) == "utf-8"


# ------------------------------------------------------------------ 登记表


REGISTRY_HEAD = (
    "defaults:\n  timeout_seconds: 9\n"
    "taxonomy:\n"
    "  field:\n    art: 艺术\n"
    "  kind:\n    media: 媒体\n"
    "  role:\n    core: 主要方向\n"
    "targets:\n"
    "  goals:\n"
    "    - id: goal-visual-narrative\n      statement: 示例第二方向\n"
    "sources:\n"
)


def _source_yaml(source_id: str, extra: str = "") -> str:
    return REGISTRY_HEAD + (
        "  - id: " + source_id + "\n    name: A\n    field: art\n    kind: media\n"
        "    role: core\n    language: en\n    serves: [goal-visual-narrative]\n"
        "    per_source: 3\n" + extra + "    indexes:\n"
        "      - url: https://example.com/list\n        pattern: \"^/p/\\\\d+$\"\n"
    )


def test_source_index_loads_and_expands_patterns(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(_source_yaml("SRC-A"), encoding="utf-8")
    data = load(path)
    source = data["sources"][0]
    assert source.per_source == 3
    assert source.pages[0].rule.pattern == r"^/p/\d+$"
    assert source.options["timeout_seconds"] == 9
    assert source.options["respect_robots"] is True
    assert source.domain == source.field == "art"


def test_source_index_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        REGISTRY_HEAD
        + "  - id: SRC-A\n    name: A\n    field: art\n    kind: media\n"
        "    role: core\n    language: en\n    serves: [goal-visual-narrative]\n"
        "    indexes:\n      - url: https://a/\n        pattern: ^/x$\n"
        "  - id: SRC-A\n    name: B\n    field: art\n    kind: media\n"
        "    role: core\n    language: en\n    serves: [goal-visual-narrative]\n"
        "    indexes:\n      - url: https://b/\n        pattern: ^/y$\n",
        encoding="utf-8",
    )
    with pytest.raises(SourceIndexError):
        load(path)


def test_source_index_rejects_facet_outside_taxonomy(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        _source_yaml("SRC-A").replace("field: art", "field: cooking"), encoding="utf-8"
    )
    with pytest.raises(SourceIndexError):
        load(path)


def test_source_index_rejects_pattern_with_lost_backslash(tmp_path):
    """d{4} 这种写法正则照样能编译，只是永远匹配不上——必须当场拦。"""
    path = tmp_path / "sources.yaml"
    path.write_text(
        REGISTRY_HEAD
        + "  - id: SRC-A\n    name: A\n    field: art\n    kind: media\n"
        "    role: core\n    language: en\n    serves: [goal-visual-narrative]\n"
        "    indexes:\n      - url: https://example.com/list\n        pattern: '^/p/d{4}$'\n",
        encoding="utf-8",
    )
    with pytest.raises(SourceIndexError):
        load(path)


def test_source_index_requires_serves(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        _source_yaml("SRC-A").replace("    serves: [goal-visual-narrative]\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(SourceIndexError):
        load(path)
