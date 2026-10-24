"""7 天去重接进 final_digest：同一件事不该第二天再出现一次。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import tools.final_digest as digest


def _write(tmp_path, selection, items):
    (tmp_path / "selection.json").write_text(json.dumps(selection, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "items.json").write_text(json.dumps(list(items.values()), ensure_ascii=False), encoding="utf-8")
    return tmp_path / "selection.json", tmp_path / "items.json"


def test_repeat_within_window_is_blocked(tmp_path, monkeypatch, capsys):
    item = {
        "item_id": "item-1",
        "title": "昨天推过的东西",
        "text": "正文足够长，用来生成摘要而不是空字符串。",
        "canonical_url": "https://example.com/a",
    }
    selection = {"core": [{"item_id": "item-1", "reason": "理由", "gist": "讲的是"}], "exploration": []}
    sel, items = _write(tmp_path, selection, {"item-1": item})

    ledger = tmp_path / "delivered.jsonl"
    ledger.write_text(
        json.dumps({
            "item_id": "item-1",
            "canonical_url": "https://example.com/a",
            "delivered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "score": 0.7,
        }, ensure_ascii=False) + chr(10),
        encoding="utf-8",
    )

    code = digest.main([
        "--selection", str(sel), "--items", str(items),
        "--out", str(tmp_path / "out"), "--ledger", str(ledger),
    ])
    assert code == 0
    md = (tmp_path / "out" / "final.md").read_text(encoding="utf-8")
    assert "### " not in md  # 全被压住


def test_no_guard_flag_bypasses(tmp_path):
    item = {
        "item_id": "item-1",
        "title": "昨天推过的东西",
        "text": "正文足够长，用来生成摘要而不是空字符串。",
        "canonical_url": "https://example.com/a",
    }
    selection = {"core": [{"item_id": "item-1", "reason": "理由", "gist": "讲的是"}], "exploration": []}
    sel, items = _write(tmp_path, selection, {"item-1": item})

    ledger = tmp_path / "delivered.jsonl"
    ledger.write_text(
        json.dumps({
            "item_id": "item-1",
            "canonical_url": "https://example.com/a",
            "delivered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "score": 0.7,
        }, ensure_ascii=False) + chr(10),
        encoding="utf-8",
    )

    code = digest.main([
        "--selection", str(sel), "--items", str(items),
        "--out", str(tmp_path / "out2"), "--ledger", str(ledger), "--no-guard",
    ])
    assert code == 0
    md = (tmp_path / "out2" / "final.md").read_text(encoding="utf-8")
    assert "item-1" in md
