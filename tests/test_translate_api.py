"""翻译上云之后的三条约定：只翻选中的、失败不阻塞、日志留证据。"""

from __future__ import annotations

import json
from pathlib import Path

import tools.translate_items as translate


def _items(path: Path) -> Path:
    rows = [
        {"item_id": "item-en-1", "title": "Hello World", "text_clean": "English body. " * 60,
         "detected_language": "en", "canonical_url": "https://example.com/1"},
        {"item_id": "item-en-2", "title": "Second", "text_clean": "Another body. " * 60,
         "detected_language": "en", "canonical_url": "https://example.com/2"},
        {"item_id": "item-zh", "title": "中文标题", "text_clean": "中文正文。" * 60,
         "detected_language": "zh", "canonical_url": "https://example.com/3"},
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def _selection(path: Path, ids: list[str]) -> Path:
    path.write_text(
        json.dumps({"selection": {"core": [{"item_id": i} for i in ids]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_only_selected_non_chinese_items_are_translated(tmp_path: Path, monkeypatch) -> None:
    items = _items(tmp_path / "items.json")
    selection = _selection(tmp_path / "selection.json", ["item-en-1", "item-zh"])
    monkeypatch.setattr(translate, "_api_key", lambda: "test-key")
    calls: list[str] = []

    def fake(text: str, *, key: str, model: str, timeout: float = 60.0) -> dict:
        calls.append(text)
        return {"text": "译文", "usage": {"prompt_tokens": 10, "completion_tokens": 2}, "ms": 5}

    monkeypatch.setattr(translate, "_api_translate", fake)
    out = tmp_path / "label"
    code = translate.main([
        "--items", str(items), "--selection", str(selection), "--out", str(out), "--workers", "2",
    ])
    assert code == 0
    assert len(calls) == 2  # 标题 + 摘要，只对 item-en-1
    data = json.loads((out / "translations.json").read_text(encoding="utf-8"))
    assert set(data) == {"item-en-1"}
    log = [
        json.loads(line)
        for line in (out / "translate.log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(log) == 2 and all(row["ok"] for row in log)


def test_failed_call_leaves_item_untranslated_and_logs_the_error(tmp_path: Path, monkeypatch) -> None:
    items = _items(tmp_path / "items.json")
    monkeypatch.setattr(translate, "_api_key", lambda: "test-key")

    def boom(*args, **kwargs):
        raise RuntimeError("HTTP 500：upstream error")

    monkeypatch.setattr(translate, "_api_translate", boom)
    out = tmp_path / "label"
    code = translate.main(["--items", str(items), "--out", str(out), "--workers", "1"])
    assert code == 0  # 不阻塞
    data = json.loads((out / "translations.json").read_text(encoding="utf-8"))
    assert data == {}  # 该条保留原文标题，不写半成品
    log = [
        json.loads(line)
        for line in (out / "translate.log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert log and all(row["ok"] is False for row in log)
    assert "HTTP 500" in log[0]["error"]
