"""信息接入的确定性部分回归测试（WP-R6 离线一半 / CAP-INF-03..05）。

保护四件事：
1. 同一篇文章的不同 URL 写法收敛成一个键；
2. 语种说不清就是 UNKNOWN，不猜；
3. 没有证据就不说"原创"，转载要能追到上游；
4. 多个转载只算一个独立来源。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ingest.dedupe import dedupe_candidates
from ingest.identity import PageSignals, identity_key, independence_count, resolve_identity
from ingest.language import detect_language
from ingest.normalize import build_candidate, content_digest, parse_published_at
from ingest.urls import canonicalize_url
from tools.validate_objects import validate

NOW = datetime.fromisoformat("2026-10-18T03:40:00+08:00")


def fetch(**overrides) -> dict:
    base = {
        "request_id": "req-fetch-0001",
        "requested_url": "https://example.org/a/b",
        "final_url": "https://example.org/a/b",
        "outcome": "FETCHED",
        "status_code": 200,
        "fetched_at": NOW.isoformat(timespec="seconds"),
        "body_ref": "store/raw/fetch-0001.html",
        "text": "Strands Decider 2B is a typed decision model that returns a label instead of generating text.",
        "title": "Strands Decider 2B: typed decision model",
        "excerpt": "A small model that answers choice questions.",
        "published_at_raw": "2026-10-17T12:00:00Z",
        "declared_language": "en",
        "canonical_link": None,
        "author_name": None,
        "site_name": "example.org",
        "error": None,
        "contract_version": "1.0",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- URL


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("HTTPS://Example.org/a/b/?utm_source=x#frag", "https://example.org/a/b"),
        ("https://example.org:443/a", "https://example.org/a"),
        ("http://example.org:80/", "http://example.org/"),
        ("https://example.org/a/?b=2&a=1", "https://example.org/a?a=1&b=2"),
        ("https://example.org/a/?fbclid=123&keep=1", "https://example.org/a?keep=1"),
    ],
)
def test_url_canonicalization(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_non_http_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonicalize_url("ftp://example.org/file")


# ---------------------------------------------------------------- 内容摘要


def test_digest_ignores_whitespace_and_case() -> None:
    assert content_digest("Hello   World") == content_digest("hello world")
    assert content_digest("hello world") != content_digest("hello worlds")


@pytest.mark.parametrize(
    "raw, ok",
    [
        ("2026-10-17T12:00:00Z", True),
        ("2026-10-17T12:00:00+08:00", True),
        ("2026-10-17", False),
        ("昨天", False),
        (None, False),
    ],
)
def test_published_at_requires_timezone(raw, ok) -> None:
    assert (parse_published_at(raw) is not None) is ok


# ---------------------------------------------------------------- 语种


def test_language_heuristic() -> None:
    assert detect_language("这是一篇中文文章，讲的是系统设计。")["tag"] == "zh-Hans"
    assert detect_language("This is an English article about typed decision models.")["tag"] == "en"
    assert detect_language("这是 mixed 文本 with English words")["tag"] == "MIXED"
    assert detect_language("12345 !!!")["tag"] == "UNKNOWN"


# ---------------------------------------------------------------- 身份


def test_canonical_link_to_other_host_confirms_upstream() -> None:
    identity = resolve_identity(
        "cand-1",
        PageSignals(
            page_host="repost.example",
            site_name="Repost",
            canonical_link_host="origin.example",
        ),
    )
    assert identity["relation"] == "REPOST"
    assert identity["upstream"]["identity_status"] == "CONFIRMED"
    assert identity["upstream"]["evidence_refs"] == ["fetch/canonical-link"]


def test_upstream_hint_is_only_probable() -> None:
    identity = resolve_identity("cand-1", PageSignals(page_host="a.example", upstream_hint="origin"))
    assert identity["relation"] == "REPOST"
    assert identity["upstream"]["identity_status"] == "PROBABLE"


def test_no_signal_means_unknown_relation() -> None:
    identity = resolve_identity("cand-1", PageSignals(page_host="a.example"))
    assert identity["relation"] == "UNKNOWN"
    assert identity["upstream"] is None


def test_reposts_of_one_origin_count_as_one_source() -> None:
    identities = [
        resolve_identity("c%d" % index, PageSignals(page_host=f"site{index}.example", canonical_link_host="origin.example"))
        for index in range(3)
    ]
    assert independence_count(identities) == 1
    assert len({identity_key(item) for item in identities}) == 1


def test_distinct_publishers_are_distinct_sources() -> None:
    identities = [
        resolve_identity("c1", PageSignals(page_host="a.example", site_name="A")),
        resolve_identity("c2", PageSignals(page_host="b.example", site_name="B")),
    ]
    assert independence_count(identities) == 2


# ---------------------------------------------------------------- 组装


def test_build_candidate_produces_a_valid_record() -> None:
    bundle = build_candidate(fetch(), candidate_id="cand-0001", observed_at=NOW)
    candidate = bundle["candidate"]
    assert validate("candidate-record", candidate) == []
    assert candidate["canonical_url"] == "https://example.org/a/b"
    assert candidate["source_language"]["method"] == "CONTENT_HEURISTIC"
    assert candidate["published_at"]["state"]["status"] == "OK"
    assert candidate["source_availability"]["outcome"] == "REACHABLE"
    assert bundle["identity"]["source_identity_id"] == "src-cand-0001"


def test_redirects_are_recorded() -> None:
    bundle = build_candidate(
        fetch(requested_url="http://example.org/old", final_url="https://example.org/new"),
        candidate_id="cand-0002",
        observed_at=NOW,
    )
    availability = bundle["candidate"]["source_availability"]
    assert availability["outcome"] == "REDIRECTED"
    assert availability["redirect_target"] == "https://example.org/new"


def test_error_fetch_still_produces_a_record() -> None:
    bundle = build_candidate(
        fetch(
            outcome="ERROR",
            body_ref=None,
            status_code=None,
            error={"status": "ERROR", "reason_code": "TIMEOUT"},
        ),
        candidate_id="cand-0003",
        observed_at=NOW,
    )
    candidate = bundle["candidate"]
    assert candidate["retrieval"]["outcome"] == "ERROR"
    assert candidate["retrieval"]["state"]["status"] == "ERROR"
    assert candidate["source_availability"]["outcome"] == "UNKNOWN"


def test_missing_title_is_rejected() -> None:
    with pytest.raises(ValueError, match="标题"):
        build_candidate(fetch(title=None), candidate_id="cand-0004", observed_at=NOW)


def test_html_language_is_used_only_when_text_is_too_short() -> None:
    short = fetch(text="hi", excerpt="hi", declared_language="de")
    bundle = build_candidate(short, candidate_id="cand-0005", observed_at=NOW)
    assert bundle["candidate"]["source_language"]["method"] == "HTML_LANG"
    assert bundle["candidate"]["source_language"]["tag"] == "de"


# ---------------------------------------------------------------- 去重


def test_dedupe_by_canonical_url() -> None:
    first = build_candidate(fetch(), candidate_id="cand-a", observed_at=NOW)["candidate"]
    second = build_candidate(
        fetch(requested_url="https://EXAMPLE.org/a/b/?utm_source=x", final_url="https://example.org/a/b/?utm_source=x"),
        candidate_id="cand-b",
        observed_at=NOW,
    )["candidate"]
    kept, duplicates = dedupe_candidates([first, second])
    assert [item["candidate_id"] for item in kept] == ["cand-a"]
    assert duplicates[0]["reason"] == "SAME_URL"


def test_dedupe_by_content_digest() -> None:
    first = build_candidate(fetch(), candidate_id="cand-a", observed_at=NOW)["candidate"]
    second = build_candidate(
        fetch(final_url="https://mirror.example/a/b", site_name="mirror.example"),
        candidate_id="cand-b",
        observed_at=NOW,
    )["candidate"]
    kept, duplicates = dedupe_candidates([first, second])
    assert len(kept) == 1
    assert duplicates[0]["reason"] == "SAME_CONTENT"


def test_distinct_content_is_kept() -> None:
    first = build_candidate(fetch(), candidate_id="cand-a", observed_at=NOW)["candidate"]
    second = build_candidate(
        fetch(
            final_url="https://example.org/other",
            title="Another article",
            text="A completely different article about visual narrative and lighting.",
        ),
        candidate_id="cand-b",
        observed_at=NOW,
    )["candidate"]
    kept, duplicates = dedupe_candidates([first, second])
    assert len(kept) == 2
    assert duplicates == []
