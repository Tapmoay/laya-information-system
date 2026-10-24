"""精确去重（确定性，不消耗模型）。

只处理两类确定性重复：同一规范化 URL、同一内容摘要。
语义重复（改标题转载）不在这里处理——那需要证据，不是字符串比较。
"""

from __future__ import annotations


def dedupe_candidates(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    ordered = sorted(candidates, key=lambda item: (item["observed_at"], item["candidate_id"]))
    kept: list[dict] = []
    duplicates: list[dict] = []
    by_url: dict[str, str] = {}
    by_digest: dict[str, str] = {}

    for candidate in ordered:
        url = candidate["canonical_url"]
        digest = candidate["content"]["digest"]
        if url in by_url:
            duplicates.append(
                {"candidate_id": candidate["candidate_id"], "duplicate_of": by_url[url], "reason": "SAME_URL"}
            )
            continue
        if digest in by_digest:
            duplicates.append(
                {"candidate_id": candidate["candidate_id"], "duplicate_of": by_digest[digest], "reason": "SAME_CONTENT"}
            )
            continue
        by_url[url] = candidate["candidate_id"]
        by_digest[digest] = candidate["candidate_id"]
        kept.append(candidate)
    return kept, duplicates
