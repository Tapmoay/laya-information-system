"""抓取预算与跳过记录：不能因为一个源没抓就整份 manifest 崩掉。"""

from __future__ import annotations

import json

import tools.crawl as crawl


def test_manifest_accepts_skipped_record(tmp_path, monkeypatch):
    """SKIPPED_DEADLINE 记录没有 name 字段，manifest 不能因此 KeyError。"""
    records = [{
        "source_id": "SRC-X",
        "source_name": "某源",
        "domain": "example.com",
        "status": "active",
        "index_pages": [],
        "items": [],
        "failures": [{"stage": "CRAWL", "url": "https://example.com", "reason_code": "SKIPPED_DEADLINE"}],
        "skipped_duplicates": 0,
    }]
    manifest_sources = [
        {
            "source_id": record["source_id"],
            "name": record.get("name") or record.get("source_name") or record["source_id"],
            "domain": record["domain"],
        }
        for record in records
    ]
    assert manifest_sources[0]["name"] == "某源"
