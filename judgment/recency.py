"""时效性：算这条内容有多旧，以及这个"旧"有多可信。

两条线要分开，混在一起就会开始编造时间：

- @@published_at@@（契约字段）**只接受带时区的完整时间**。时区不明会把"昨天"算成今天，
  这类错误比缺字段更难发现，所以它宁可记 UNKNOWN。
- 这里的 @@estimate()@@ 是给**降权**用的粗略年龄，允许只有日期、允许从 URL 里取，
  但必须把 @@precision@@ 一起返回。日精度对"这条是不是两周前的旧闻"够用，
  对"它比另一条早三小时"不够用——所以它绝不回填 @@published_at@@。

三条禁止（和 capability CAP-MEA-04 的 forbidden 一致）：
  1. 不把无法解析的发布时间当作最新；
  2. 不用抓取时间替代发布时间；
  3. 不把 MONTH 精度的日期说成 DAY 精度。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
URL_YMD = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$|[^\d])")
URL_YMD_DASH = re.compile(r"/(20\d{2})-(\d{2})-(\d{2})-")
URL_YM_EN = re.compile(r"/(20\d{2})/([A-Z][a-z]{2})/(\d{1,2})(?:/|$)")
URL_YM = re.compile(r"/(20\d{2})/(\d{1,2})(?:/|$)")
# arXiv 编号自带年月：2610.22350 = 2026 年 10 月。论文页与 HF Papers 页都有，
# 比页面上的 "Published on Oct 13"（经常没有年份）可靠得多。
ARXIV_ID = re.compile(r"\b(\d{2})(\d{2})\.\d{4,5}\b")
CJK_DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
EN_DATE_DMY = re.compile(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(20\d{2})")
EN_DATE_MDY = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2})")
# 太老或未来的日期多半是解析错了（页脚版权年、示例日期、图片年份）。
# 宁可记 UNKNOWN 也不要拿一个明显错的日期去降权。
MAX_AGE_DAYS = 3 * 365
MAX_FUTURE_DAYS = 1
MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _result(value: datetime | None, precision: str, source: str) -> dict[str, Any]:
    return {"value": value, "precision": precision, "source": source}


def estimate(
    *,
    published_raw: str | None,
    url: str | None,
    text: str | None,
) -> dict[str, Any]:
    """按可靠性从高到低估计发布时间。返回 {value, precision, source}。"""
    if published_raw:
        try:
            parsed = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return _result(parsed, "SECOND", "META_ISO")
        match = ISO_DATE.match(published_raw.strip())
        if match:
            year, month, day = (int(part) for part in match.groups())
            return _result(datetime(year, month, day, 12, tzinfo=timezone.utc), "DAY", "META_DATE")

    candidate_url = url or ""
    arxiv = ARXIV_ID.search(candidate_url) or ARXIV_ID.search((text or "")[:600])
    if arxiv:
        year, month = 2000 + int(arxiv.group(1)), int(arxiv.group(2))
        if 1 <= month <= 12:
            return _result(datetime(year, month, 1, 12, tzinfo=timezone.utc), "MONTH", "ARXIV_ID")
    for pattern, kind in ((URL_YMD, "YMD"), (URL_YMD_DASH, "YMD")):
        match = pattern.search(candidate_url)
        if match:
            year, month, day = (int(part) for part in match.groups())
            if 1 <= month <= 12 and 1 <= day <= 31:
                return _result(datetime(year, month, day, 12, tzinfo=timezone.utc), "DAY", "URL_DATE")
    match = URL_YM_EN.search(candidate_url)
    if match:
        year, name, day = int(match.group(1)), match.group(2), int(match.group(3))
        month = MONTHS.get(name)
        if month and 1 <= day <= 31:
            return _result(datetime(year, month, day, 12, tzinfo=timezone.utc), "DAY", "URL_DATE")
    match = URL_YM.search(candidate_url)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return _result(datetime(year, month, 1, 12, tzinfo=timezone.utc), "MONTH", "URL_MONTH")

    head = (text or "")[:1500]
    match = CJK_DATE.search(head)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return _result(datetime(year, month, day, 12, tzinfo=timezone.utc), "DAY", "TEXT_DATE")
    match = EN_DATE_DMY.search(head)
    if match:
        day, name, year = int(match.group(1)), match.group(2), int(match.group(3))
        return _result(datetime(year, MONTHS[name], day, 12, tzinfo=timezone.utc), "DAY", "TEXT_DATE")
    match = EN_DATE_MDY.search(head)
    if match:
        name, day, year = match.group(1), int(match.group(2)), int(match.group(3))
        return _result(datetime(year, MONTHS[name], day, 12, tzinfo=timezone.utc), "DAY", "TEXT_DATE")

    return _result(None, "UNKNOWN", "UNKNOWN")


def age_days(stamp: datetime | None, observed_at: datetime) -> float | None:
    if stamp is None:
        return None
    reference = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=timezone.utc)
    delta = reference - stamp
    return round(delta.total_seconds() / 86400.0, 2)


def measure(item: dict[str, Any], observed_at: datetime) -> dict[str, Any]:
    """对一个抓取条目算时效，供打分与终选包使用。"""
    published = (item.get("published_at") or {}).get("value")
    guess = estimate(
        published_raw=published,
        url=item.get("canonical_url"),
        text=item.get("text_clean") or item.get("text"),
    )
    age = age_days(guess["value"], observed_at)
    reason = None
    if age is not None and age > MAX_AGE_DAYS:
        age, reason = None, "DATE_TOO_OLD"
    elif age is not None and age < -MAX_FUTURE_DAYS:
        age, reason = None, "DATE_IN_FUTURE"
    return {
        "age_days": age,
        "rejected_reason": reason,
        "published_estimate": guess["value"].isoformat() if guess["value"] else None,
        "precision": guess["precision"],
        "source": guess["source"],
        # 契约字段本身是否已知，和上面那个估计是两件事，不要混
        "published_at_known": bool(published),
    }
