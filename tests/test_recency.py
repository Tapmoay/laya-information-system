"""时效：未知就是未知，不许拿抓取时间顶替发布时间。"""

from __future__ import annotations

from datetime import datetime, timezone

from judgment.recency import age_days, estimate, measure

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def test_iso_with_timezone_is_exact():
    result = estimate(published_raw="2026-10-17T00:00:00Z", url=None, text=None)
    assert result["precision"] == "SECOND" and result["source"] == "META_ISO"
    assert age_days(result["value"], NOW) == 2.5


def test_date_only_is_day_precision():
    result = estimate(published_raw="2026-10-17", url=None, text=None)
    assert result["precision"] == "DAY" and result["source"] == "META_DATE"


def test_url_date_is_used_when_meta_missing():
    result = estimate(published_raw=None, url="https://www.qbitai.com/2026/10/17/504067.html", text=None)
    assert result["precision"] == "DAY" and result["source"] == "URL_DATE"


def test_dashed_url_date():
    result = estimate(published_raw=None, url="https://lilianweng.github.io/posts/2026-07-04-harness/", text=None)
    assert result["source"] == "URL_DATE"


def test_arxiv_id_gives_month():
    result = estimate(published_raw=None, url="https://arxiv.org/abs/2610.22350", text=None)
    assert result["precision"] == "MONTH" and result["source"] == "ARXIV_ID"


def test_chinese_date_in_text():
    result = estimate(published_raw=None, url=None, text="发布于 2026年10月15日，作者……")
    assert result["precision"] == "DAY" and result["source"] == "TEXT_DATE"


def test_english_date_in_text():
    result = estimate(published_raw=None, url=None, text="Submitted on 17 Oct 2026 by someone")
    assert result["precision"] == "DAY" and result["source"] == "TEXT_DATE"


def test_nothing_known_stays_unknown():
    result = estimate(published_raw=None, url="https://example.com/a", text="没有日期")
    assert result["value"] is None and result["precision"] == "UNKNOWN"


def test_future_date_is_rejected_not_trusted():
    item = {"published_at": {"value": None}, "canonical_url": "https://x.com/", "text": "2027年12月31日"}
    result = measure(item, NOW)
    assert result["age_days"] is None
    assert result["rejected_reason"] == "DATE_IN_FUTURE"


def test_absurdly_old_date_is_rejected():
    item = {"published_at": {"value": "2016-01-01T00:00:00Z"}, "canonical_url": "https://x.com/"}
    result = measure(item, NOW)
    assert result["age_days"] is None
    assert result["rejected_reason"] == "DATE_TOO_OLD"
