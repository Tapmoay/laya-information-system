"""台账落账必须晚于推送：没发出去的不该占 7 天窗口。"""

from __future__ import annotations

import json
from pathlib import Path

import tools.push_markdown as push


def _staging(dir_path: Path, rows: list[dict]) -> Path:
    path = dir_path / "ledger-pending.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return path


def test_only_successful_items_are_committed(tmp_path, monkeypatch):
    (tmp_path / "store").mkdir()
    monkeypatch.setattr(push, "ROOT", tmp_path)
    _staging(tmp_path, [{"item_id": "a"}, {"item_id": "b"}, {"item_id": "c"}])

    written = push.commit_ledger_items(tmp_path / "final.md", {"a", "c"})
    assert written == 2
    ledger = tmp_path / "store" / "delivered.jsonl"
    kept = [json.loads(line)["item_id"] for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert kept == ["a", "c"]
    remaining = (tmp_path / "ledger-pending.jsonl").read_text(encoding="utf-8")
    assert '"item_id": "b"' in remaining
    assert '"item_id": "a"' not in remaining


def test_missing_staging_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "ROOT", tmp_path)
    assert push.commit_ledger_items(tmp_path / "final.md", {"a"}) == 0
