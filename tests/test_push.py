"""推送渠道：邮箱 / 文件；dry-run 不发送；按 item_id 记账。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from store import repeat_guard
from tools import push, push_markdown, user_config

FINAL = """# 本期读物

## 通道 A　目标驱动

### 1. 第一条

**AI 摘要**：摘要一……

来源：X / y　·　链接：https://example.com/a
为什么选它：能推进当前任务
item_id：item-a

### 2. 第二条

来源：X / y　·　链接：https://example.com/b
为什么选它：能改变判断
item_id：item-b

## 通道 C　受控探索

### 1. 第三条

来源：X / z　·　链接：https://example.com/c
为什么选它：跨领域的同一结构
item_id：item-c
"""


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    final = final_dir / "final.md"
    final.write_text(FINAL, encoding="utf-8")
    pending = final_dir / "ledger-pending.jsonl"
    pending.write_text(
        "\n".join(
            json.dumps({"item_id": item_id, "title": item_id}, ensure_ascii=False)
            for item_id in ("item-a", "item-b", "item-c")
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "user.yaml"
    data = user_config.deep_merge(
        user_config.DEFAULTS,
        {
            "delivery": {
                "channel": "email",
                "email": {
                    "host": "smtp.example.com",
                    "port": 587,
                    "username": "me@example.com",
                    "from": "me@example.com",
                    "to": ["me@example.com"],
                    "password_env": "TEST_EMAIL_PASSWORD",
                },
            }
        },
    )
    config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return final, config, pending


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port
        self.message = None
        self.logged_in = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.message = message


def test_email_sends_one_message_and_commits_all_items(tmp_path: Path, monkeypatch) -> None:
    final, config, pending = _fixture(tmp_path)
    ledger = tmp_path / "delivered.jsonl"
    FakeSMTP.instances.clear()
    monkeypatch.setattr(repeat_guard, "DEFAULT_LEDGER", str(ledger))
    monkeypatch.setattr(push.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(push, "_secret", lambda name: "授权码")
    code = push.main(["--from-final", str(final), "--config", str(config)])
    assert code == 0
    sent = FakeSMTP.instances[-1]
    assert sent.host == "smtp.example.com"
    assert sent.message["To"] == "me@example.com"
    assert "3 条" in sent.message["Subject"]
    assert sent.logged_in == ("me@example.com", "授权码")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert {row["item_id"] for row in rows} == {"item-a", "item-b", "item-c"}
    assert not pending.exists()


def test_email_dry_run_needs_no_password_and_sends_nothing(tmp_path: Path, monkeypatch) -> None:
    final, _, _ = _fixture(tmp_path)
    entries = push._entries(final)
    data = user_config.load()
    data["delivery"]["channel"] = "email"
    data["delivery"]["email"].update(
        {"host": "smtp.example.com", "from": "me@example.com", "to": ["me@example.com"]}
    )
    monkeypatch.setattr(push, "_secret", lambda name: "")
    FakeSMTP.instances.clear()
    assert push.send_email(entries, data, dry_run=True) == 0
    assert FakeSMTP.instances == []


def test_ledger_commit_is_by_item_id_not_row_order(tmp_path: Path, monkeypatch) -> None:
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    final = final_dir / "final.md"
    final.write_text(FINAL, encoding="utf-8")
    (final_dir / "ledger-pending.jsonl").write_text(
        "\n".join(
            json.dumps({"item_id": item_id}, ensure_ascii=False)
            for item_id in ("item-a", "item-b", "item-c")
        )
        + "\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "delivered.jsonl"
    monkeypatch.setattr(repeat_guard, "DEFAULT_LEDGER", str(ledger))
    written = push_markdown.commit_ledger_items(final, {"item-c"})
    assert written == 1
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["item_id"] for row in rows] == ["item-c"]
    remaining = (final_dir / "ledger-pending.jsonl").read_text(encoding="utf-8")
    assert "item-a" in remaining and "item-b" in remaining and "item-c" not in remaining


def test_file_channel_copies_and_does_not_commit_ledger(tmp_path: Path, monkeypatch) -> None:
    final, _, _ = _fixture(tmp_path)
    entries = push._entries(final)
    data = user_config.deep_merge(
        user_config.DEFAULTS,
        {"delivery": {"channel": "file", "file": {"outbox": str(tmp_path / "outbox")}}},
    )
    ledger = tmp_path / "delivered.jsonl"
    monkeypatch.setattr(repeat_guard, "DEFAULT_LEDGER", str(ledger))
    assert push.send_file(entries, data, final) == 0
    assert (tmp_path / "outbox" / f"final-{__import__('datetime').date.today()}.md").exists()
    assert not ledger.exists()


def test_email_content_keeps_link_and_reason(tmp_path: Path) -> None:
    final, _, _ = _fixture(tmp_path)
    subject, text, html = push.email_content(push._entries(final), "每日读物")
    assert "3 条" in subject
    assert "https://example.com/a" in text
    assert "为什么给你" in html
    assert "<a href='https://example.com/a'>原文</a>" in html
