"""无人值守 runner：只测纯函数，不真的拉起 dsh。"""

from __future__ import annotations

from pathlib import Path

import tools.run_daily as daily


def test_runbook_exists_and_mentions_push():
    text = (daily.ROOT / "config" / "daily-runbook.md").read_text(encoding="utf-8")
    assert "tools/push.py" in text
    assert "gist" in text


def test_api_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert daily._api_key() == "sk-env"


def test_missing_credentials_file_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    try:
        daily._api_key()
    except SystemExit as exc:
        assert "凭据" in str(exc)
    else:
        raise AssertionError("应该报错")
