"""FetchResult → CandidateRecord（CAP-INF-03）。

规范化只做确定性的事：URL 收敛、内容摘要、时间解析、语种观测、来源身份。
不判断相关性，也不决定要不要推荐。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from ingest.identity import PageSignals, resolve_identity
from ingest.language import detect_language
from ingest.urls import canonicalize_url, host_of
from tools.validate_objects import validate

WHITESPACE = re.compile(r"\s+")


def content_digest(text: str) -> str:
    normalized = WHITESPACE.sub(" ", text).strip().casefold()
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_published_at(raw: str | None) -> datetime | None:
    """只接受带时区的时间。

    只有日期（`2026-10-17`）或没有偏移的时间不算"知道发布时间"——
    时区不明会把"昨天"算成今天，这类错误比缺字段更难发现。
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _availability(status_code: int | None, redirected: bool) -> str:
    if redirected:
        return "REDIRECTED"
    if status_code is None:
        return "UNKNOWN"
    if 200 <= status_code < 300:
        return "REACHABLE"
    if status_code in (404, 410):
        return "GONE"
    if status_code in (401, 402, 403):
        return "PAYWALLED"
    return "UNKNOWN"


def _language(fetch: dict[str, Any], text: str) -> dict[str, Any]:
    observed = detect_language(text)
    declared = fetch.get("declared_language")
    if observed["tag"] != "UNKNOWN" and len(text) >= 40:
        return observed
    if declared and declared not in {"MIXED", "UNKNOWN"}:
        return {"tag": declared, "method": "HTML_LANG", "confidence": None}
    return observed


def build_candidate(
    fetch: dict[str, Any],
    *,
    candidate_id: str,
    observed_at: datetime,
    discovery_method: str = "SEARCH",
    query_ref: str | None = None,
    monitoring_profile_ref: str | None = None,
) -> dict[str, Any]:
    problems = validate("fetch-result", fetch)
    if problems:
        raise ValueError("抓取结果违反契约：" + "; ".join(problems))

    title = fetch.get("title")
    if not title:
        raise ValueError("候选缺少标题，无法生成 CandidateRecord")

    text = fetch.get("text") or fetch.get("excerpt") or title
    canonical_url = canonicalize_url(fetch["final_url"])
    redirected = canonical_url != canonicalize_url(fetch["requested_url"])

    published_value = parse_published_at(fetch.get("published_at_raw"))
    published_at = (
        {"value": published_value.isoformat(), "state": {"status": "OK"}}
        if published_value
        else {"value": None, "state": {"status": "UNKNOWN", "reason_code": "EVIDENCE_INSUFFICIENT"}}
    )

    language = _language(fetch, text)
    identity = resolve_identity(
        candidate_id,
        PageSignals(
            page_host=host_of(canonical_url),
            site_name=fetch.get("site_name"),
            author_name=fetch.get("author_name"),
            canonical_link_host=host_of(fetch["canonical_link"]) if fetch.get("canonical_link") else None,
        ),
    )

    outcome = fetch["outcome"]
    error = fetch.get("error")
    if outcome == "ERROR" and not error:
        raise ValueError("outcome=ERROR 时必须提供 error 状态")

    candidate = {
        "candidate_id": candidate_id,
        "title": {"text": title, "language": language["tag"]},
        "candidate_type": "ARTICLE",
        "canonical_url": canonical_url,
        "publisher": identity["publisher"],
        "upstream": identity["upstream"],
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "published_at": published_at,
        "source_language": language,
        "content": {
            "digest": content_digest(text),
            "text_ref": fetch["body_ref"] or f"store/raw/{candidate_id}",
            "excerpt_ref": None,
            "byte_length": len(text.encode("utf-8")),
            "storage_policy": "LICENSED_EXCERPT",
        },
        "raw_claims": [],
        "discovery": {
            "method": discovery_method,
            "discovered_at": observed_at.isoformat(timespec="seconds"),
            "query_ref": query_ref,
            "monitoring_profile_ref": monitoring_profile_ref,
        },
        "retrieval": {
            "outcome": outcome,
            "attempted_at": fetch["fetched_at"],
            "http_status": fetch["status_code"],
            "attempts": 1,
            "state": error if outcome == "ERROR" else None,
        },
        "source_availability": {
            "outcome": _availability(fetch["status_code"], redirected),
            "checked_at": fetch["fetched_at"],
            "redirect_target": canonical_url if redirected else None,
        },
        "contract_version": "1.0",
    }

    problems = validate("candidate-record", candidate)
    if problems:
        raise ValueError("候选记录违反契约：" + "; ".join(problems))
    return {"candidate": candidate, "identity": identity, "language": language}
