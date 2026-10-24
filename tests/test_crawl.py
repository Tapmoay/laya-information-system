"""抓取管线：取证链必须完整，失败必须留痕（全程不打网络）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ingest.source_index import DEFAULT_SOURCE, IndexPage, Source
from ingest.discover import LinkRule
import tools.crawl as crawl

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)
INDEX_HTML = """
<html><body>
<a href="/p/1">第一篇文章的标题足够长</a>
<a href="/p/2">第二篇文章的标题足够长</a>
<a href="/about">关于</a>
</body></html>
"""


def _fetch_result(url: str, *, title: str = "标题", text: str = "正文内容" * 40) -> dict:
    return {
        "request_id": "req",
        "requested_url": url,
        "final_url": url,
        "outcome": "FETCHED",
        "status_code": 200,
        "fetched_at": NOW.isoformat(timespec="seconds"),
        "body_ref": "raw/x.html",
        "text": text,
        "title": title,
        "excerpt": None,
        "published_at_raw": None,
        "declared_language": "zh-Hans",
        "canonical_link": None,
        "author_name": None,
        "site_name": "example",
        "error": None,
        "contract_version": "1.0",
    }


def _source(per_source: int = 5, network: str = "direct") -> Source:
    return Source(
        id="SRC-T",
        name="测试源",
        field="art",
        kind="media",
        role="core",
        language="zh-Hans",
        density="medium",
        network=network,
        status="VERIFIED",
        why="测试",
        serves=("goal-visual-narrative",),
        per_source=per_source,
        pages=(IndexPage(url="https://example.com/", rule=LinkRule(pattern=r"^/p/\d+$")),),
        options=dict(DEFAULT_SOURCE),
    )


def test_crawl_builds_full_provenance_chain(tmp_path, monkeypatch):
    def fake(url, *, request_id, policy, now=None):
        if url.rstrip("/") == "https://example.com":
            return {"result": _fetch_result(url, title="列表"), "html": INDEX_HTML}
        return {"result": _fetch_result(url, title="文章 " + url), "html": "<html></html>"}

    monkeypatch.setattr(crawl, "fetch_page", fake)
    record = crawl.crawl_source(_source(), out=tmp_path, now=NOW)

    assert len(record["items"]) == 2
    item = record["items"][0]
    chain = item["provenance"]
    assert chain["index_url"] == "https://example.com/"
    assert chain["index_fetched_at"] == NOW.isoformat(timespec="seconds")
    assert chain["article_fetched_at"] == NOW.isoformat(timespec="seconds")
    assert chain["http_status"] == 200
    assert chain["content_digest"].startswith("sha256:")
    # 原始正文与去导航正文都要留：前者用于取证，后者用于测量
    assert item["text_clean"]
    assert "chrome_removed_ratio" in item
    assert (tmp_path / "raw" / (item["item_id"] + ".html")).exists()


def test_item_id_is_stable_across_crawls():
    first = crawl.item_id_for("https://example.com/p/1")
    second = crawl.item_id_for("https://example.com/p/1/?utm_source=x")
    assert first == second


def test_budget_is_respected(tmp_path, monkeypatch):
    def fake(url, *, request_id, policy, now=None):
        if url.rstrip("/") == "https://example.com":
            return {"result": _fetch_result(url), "html": INDEX_HTML}
        return {"result": _fetch_result(url), "html": "<html></html>"}

    monkeypatch.setattr(crawl, "fetch_page", fake)
    record = crawl.crawl_source(_source(per_source=1), out=tmp_path, now=NOW)
    assert len(record["items"]) == 1


def test_article_failure_is_recorded_not_hidden(tmp_path, monkeypatch):
    def fake(url, *, request_id, policy, now=None):
        if url.rstrip("/") == "https://example.com":
            return {"result": _fetch_result(url), "html": INDEX_HTML}
        return {
            "result": {
                "request_id": "req",
                "requested_url": url,
                "final_url": url,
                "outcome": "ERROR",
                "status_code": 404,
                "fetched_at": NOW.isoformat(timespec="seconds"),
                "body_ref": None,
                "error": {"status": "ERROR", "reason_code": "SOURCE_GONE"},
                "contract_version": "1.0",
            },
            "html": None,
        }

    monkeypatch.setattr(crawl, "fetch_page", fake)
    record = crawl.crawl_source(_source(), out=tmp_path, now=NOW)
    assert record["items"] == []
    assert len(record["failures"]) == 2
    assert record["failures"][0]["reason_code"] == "SOURCE_GONE"


def test_duplicate_across_sources_is_skipped(tmp_path, monkeypatch):
    def fake(url, *, request_id, policy, now=None):
        if url.rstrip("/") == "https://example.com":
            return {"result": _fetch_result(url), "html": INDEX_HTML}
        return {"result": _fetch_result(url), "html": "<html></html>"}

    monkeypatch.setattr(crawl, "fetch_page", fake)
    seen: set[str] = set()
    first = crawl.crawl_source(_source(), out=tmp_path, now=NOW, seen_urls=seen)
    second = crawl.crawl_source(_source(), out=tmp_path, now=NOW, seen_urls=seen)
    assert len(first["items"]) == 2
    assert second["items"] == []
    assert second["skipped_duplicates"] == 2


def test_empty_index_page_is_recorded_not_silently_zero(tmp_path, monkeypatch):
    """列表页能打开、却一条都没命中，必须留痕。

    实测来源：research.checkpoint.com 会回 202 挑战页，锚点数为 0。
    如果不记这一条，抓取结果看起来和"这个站今天没更新"一模一样。
    """

    def fake(url, *, request_id, policy, now=None):
        if url.rstrip("/") == "https://example.com":
            return {"result": _fetch_result(url, title="空壳"), "html": "<html><body>challenge</body></html>"}
        return {"result": _fetch_result(url), "html": "<html></html>"}

    monkeypatch.setattr(crawl, "fetch_page", fake)
    record = crawl.crawl_source(_source(), out=tmp_path, now=NOW)

    assert record["items"] == []
    assert [item["reason_code"] for item in record["failures"]] == ["INDEX_EMPTY"]
    assert record["failures"][0]["anchors"] == 0
    assert record["index_pages"][0]["counts"]["selected"] == 0


def test_source_needing_proxy_is_reported_not_treated_as_unreachable(tmp_path, monkeypatch):
    """缺代理 ≠ 抓不到。前者加一句 --proxy 就能进，后者加代理也没用。"""

    def fake(url, *, request_id, policy, now=None):
        raise AssertionError("缺代理时不该发起任何请求")

    monkeypatch.setattr(crawl, "fetch_page", fake)
    record = crawl.crawl_source(_source(network="proxy"), out=tmp_path, now=NOW)

    assert record["items"] == []
    assert [item["reason_code"] for item in record["failures"]] == ["NEEDS_PROXY"]
