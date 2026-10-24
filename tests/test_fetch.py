"""受控抓取回归测试（WP-R6 / CAP-INF-02）。

全部测试跑在本机 HTTP 服务器上，不依赖公网：
抓取路径、重试、超时、白名单、robots、字节上限都是真实 HTTP 行为，不是打桩。
"""

from __future__ import annotations

import functools
import http.server
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ingest.fetch import FetchPolicy, fetch
from ingest.html_extract import extract
from tools.validate_objects import validate

ROOT = Path(__file__).resolve().parents[1]
HTML_DIR = ROOT / "fixtures" / "html"
NOW = datetime.fromisoformat("2026-10-18T04:10:00+08:00")


class Handler(http.server.SimpleHTTPRequestHandler):
    flaky_hits = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HTML_DIR), **kwargs)

    def log_message(self, *args):  # 静音
        return

    def do_GET(self):
        if self.path.startswith("/gzip"):
            import gzip as _gzip

            payload = _gzip.compress((HTML_DIR / "article.html").read_bytes())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/flaky"):
            type(self).flaky_hits += 1
            if type(self).flaky_hits <= 2:
                self.send_error(500, "transient")
                return
            self.path = "/article.html"
        super().do_GET()


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def policy(**overrides) -> FetchPolicy:
    base = dict(
        allowed_hosts=("127.0.0.1",),
        timeout_seconds=5,
        max_attempts=3,
        max_bytes=200_000,
        respect_robots=True,
        retry_backoff_seconds=0.01,
    )
    base.update(overrides)
    return FetchPolicy(**base)


# ---------------------------------------------------------------- 抽取


def test_extract_prefers_open_graph_and_drops_scripts() -> None:
    fields = extract((HTML_DIR / "article.html").read_text(encoding="utf-8"))
    assert fields["title"] == "Strands Decider 2B: typed decision model"
    assert fields["canonical_link"] == "https://origin.example/strands-decider-2b"
    assert fields["published_at_raw"] == "2026-10-17T12:00:00Z"
    assert fields["author_name"] == "A. Writer"
    assert fields["site_name"] == "example.org"
    assert fields["declared_language"] == "en"
    assert "should not appear" not in fields["text"]
    assert "First paragraph" in fields["text"]


# ---------------------------------------------------------------- 抓取


def test_fetch_returns_a_valid_result(server: str) -> None:
    result = fetch(f"{server}/article.html", request_id="req-1", policy=policy(), now=NOW)
    assert validate("fetch-result", result) == []
    assert result["outcome"] == "FETCHED"
    assert result["status_code"] == 200
    assert result["title"].startswith("Strands")
    assert result["canonical_link"] == "https://origin.example/strands-decider-2b"
    assert result["declared_language"] == "en"
    assert result["error"] is None


def test_fetch_404_is_gone(server: str) -> None:
    result = fetch(f"{server}/missing.html", request_id="req-2", policy=policy(), now=NOW)
    assert result["outcome"] == "ERROR"
    assert result["status_code"] == 404
    assert result["error"]["reason_code"] == "SOURCE_GONE"


def test_robots_disallow_is_respected(server: str) -> None:
    result = fetch(f"{server}/private/secret.html", request_id="req-3", policy=policy(), now=NOW)
    assert result["outcome"] == "ERROR"
    assert result["error"]["reason_code"] == "ACCESS_DISALLOWED"


def test_host_outside_allowlist_is_refused_without_connecting() -> None:
    result = fetch("https://example.com/post", request_id="req-4", policy=policy(), now=NOW)
    assert result["outcome"] == "ERROR"
    assert result["error"]["reason_code"] == "ACCESS_DISALLOWED"


def test_empty_allowlist_denies_everything(server: str) -> None:
    result = fetch(f"{server}/article.html", request_id="req-5", policy=policy(allowed_hosts=()), now=NOW)
    assert result["outcome"] == "ERROR"
    assert result["error"]["reason_code"] == "ACCESS_DISALLOWED"


def test_byte_limit_truncates_instead_of_failing(server: str) -> None:
    result = fetch(f"{server}/big.html", request_id="req-6", policy=policy(max_bytes=500), now=NOW)
    assert result["outcome"] == "PARTIAL_FETCH"
    assert result["status_code"] == 200


def test_transient_500_is_retried(server: str) -> None:
    Handler.flaky_hits = 0
    result = fetch(f"{server}/flaky", request_id="req-7", policy=policy(), now=NOW)
    assert result["outcome"] == "FETCHED"
    assert Handler.flaky_hits == 3


def test_utf8_chinese_page(server: str) -> None:
    result = fetch(f"{server}/chinese.html", request_id="req-8", policy=policy(), now=NOW)
    assert result["outcome"] == "FETCHED"
    assert result["declared_language"] == "zh-CN"
    assert "光照" in result["text"]


def test_gzip_response_is_decoded(server: str) -> None:
    result = fetch(f"{server}/gzip.html", request_id="req-9", policy=policy(), now=NOW)
    assert result["outcome"] == "FETCHED"
    assert result["title"].startswith("Strands")
    assert "First paragraph" in result["text"]


def test_gzip_truncation_counts_decoded_bytes(server: str) -> None:
    result = fetch(f"{server}/gzip.html", request_id="req-10", policy=policy(max_bytes=200), now=NOW)
    assert result["outcome"] == "PARTIAL_FETCH"
    assert result["error"] is None


def test_policy_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        FetchPolicy.from_mapping({"sources": {"allowed_hosts": ["a"], "surprise": 1}})


def test_heading_wins_over_prefixed_site_title():
    """Hugging Face 的论文页标题是「Paper page - 真正的标题」，那个前缀不该进译文。"""
    html = (
        "<html><head><title>Paper page - Waypoint-OPD: Transferring Policy Shifts</title>"
        '<meta property="og:title" content="Paper page - Waypoint-OPD: Transferring Policy Shifts">'
        "</head><body><h1>Waypoint-OPD: Transferring Policy Shifts</h1></body></html>"
    )
    assert extract(html)["title"] == "Waypoint-OPD: Transferring Policy Shifts"


def test_heading_does_not_override_an_unrelated_title():
    """h1 不是标题的子串时不替换：很多站的 h1 是栏目名，猜错就毁掉整个标题。"""
    html = (
        "<html><head><title>How to Write a Contract</title></head>"
        "<body><h1>Engineering Blog</h1></body></html>"
    )
    assert extract(html)["title"] == "How to Write a Contract"


def test_heading_does_not_override_when_it_is_too_short():
    html = (
        "<html><head><title>Notes - A Very Long Article Title About Systems</title></head>"
        "<body><h1>Notes</h1></body></html>"
    )
    assert extract(html)["title"] == "Notes - A Very Long Article Title About Systems"
