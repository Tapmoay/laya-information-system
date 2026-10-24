"""把 final.md 推到用户选的渠道：邮箱 / 文件 / 不推。

渠道是个人环境，不是判断规则。内置三个：

- `email`：一封 HTML 邮件，SMTP；
- `file`：复制到 outbox，不发送、不记账；
- `none`：只保留 final.md。

要加企业微信、Telegram、Slack 等渠道：按
`agent/skills/personal-information-environment/references/adaptation.md`
的适配器契约在 `main` 里加一个分支；不要改判断层。

四条约束：

1. **dry-run 不发送、不记账**；
2. **按 item_id 记账**，不按行号；
3. **失败不记账**：只对真正送达的条目落 7 天窗口；
4. **file 渠道不记账**：产物在 final.md，是否算"送达"由人决定。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from tools import push_markdown, user_config  # noqa: E402

CIRCLED = push_markdown.CIRCLED


def _entries(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    entries = push_markdown.parse_final(path)
    if limit:
        entries = entries[:limit]
    return entries


def _secret(name: str) -> str:
    """授权码/密码只从环境变量或 ~/.dsh/.credentials.yaml 读，不写进配置。"""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    path = Path(os.path.expanduser("~")) / ".dsh" / ".credentials.yaml"
    if not path.exists():
        return ""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ""
    return str(((data.get("refs") or {}).get(name) or "")).strip()


def _recipients(data: dict[str, Any]) -> list[str]:
    raw = user_config.get(data, "delivery.email.to") or []
    if isinstance(raw, str):
        raw = raw.replace("；", ",").replace(";", ",").split(",")
    return [str(item).strip() for item in raw if str(item).strip()]


def email_content(entries: list[dict[str, Any]], prefix: str) -> tuple[str, str, str]:
    date = datetime.now().strftime("%Y-%m-%d")
    subject = f"{prefix} {date}（{len(entries)} 条）"
    text_lines = [f"今日读物 · {date} · {len(entries)} 条", ""]
    html_parts = [
        "<div style='font-family:-apple-system,Segoe UI,sans-serif;line-height:1.55'>",
        f"<h2>今日读物 · {len(entries)} 条</h2>",
    ]
    for index, entry in enumerate(entries, 1):
        marker = CIRCLED[index - 1] if index <= len(CIRCLED) else f"{index}."
        title = str(entry.get("title") or "")
        section = push_markdown.channel_label(str(entry.get("section") or ""))
        summary = push_markdown._gist(str(entry.get("summary") or ""))
        reason = str(entry.get("reason") or "")
        link = str(entry.get("link") or "")
        text_lines += [f"{marker} {title}", section]
        if summary:
            text_lines.append("> " + summary)
        if reason:
            text_lines.append("为什么给你：" + reason)
        if link:
            text_lines.append("原文：" + link)
        text_lines.append("")

        html_parts.append(f"<h3>{html.escape(marker)} {html.escape(title)}</h3>")
        html_parts.append(f"<p style='color:#666'>{html.escape(section)}</p>")
        if summary:
            html_parts.append(
                "<blockquote style='border-left:3px solid #ddd;margin:0;padding-left:10px;color:#444'>"
                + html.escape(summary)
                + "</blockquote>"
            )
        if reason:
            html_parts.append("<p><b>为什么给你：</b>" + html.escape(reason) + "</p>")
        if link:
            html_parts.append(f"<p><a href='{html.escape(link, quote=True)}'>原文</a></p>")
    html_parts.append("</div>")
    return subject, "\n".join(text_lines), "\n".join(html_parts)


def send_email(entries: list[dict[str, Any]], data: dict[str, Any], *, dry_run: bool) -> int:
    email = user_config.get(data, "delivery.email") or {}
    host = str(email.get("host") or "").strip()
    sender = str(email.get("from") or "").strip()
    recipients = _recipients(data)
    if not host or not sender or not recipients:
        print("邮箱配置不完整：需要 delivery.email.host / from / to", file=sys.stderr)
        return 2
    password_env = str(email.get("password_env") or "LAYA_EMAIL_PASSWORD")
    password = _secret(password_env)
    if not password and not dry_run:
        print(
            f"缺少邮箱授权码：设置环境变量 {password_env}，或写进 ~/.dsh/.credentials.yaml 的 refs.{password_env}",
            file=sys.stderr,
        )
        return 2

    subject, text, html_body = email_content(entries, str(email.get("subject_prefix") or "每日读物"))
    if dry_run:
        print(f"收件人：{', '.join(recipients)}")
        print(f"主题：{subject}")
        print("-" * 60)
        print(text)
        return 0

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    port = int(email.get("port") or 587)
    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if bool(email.get("starttls", True)):
                server.starttls()
                server.ehlo()
            username = str(email.get("username") or "").strip()
            if username:
                server.login(username, password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        print(f"邮件发送失败：{exc}", file=sys.stderr)
        return 1
    print(f"已发送邮件：{subject} → {', '.join(recipients)}")
    return 0


def send_file(entries: list[dict[str, Any]], data: dict[str, Any], final_path: Path) -> int:
    outbox = ROOT / str(user_config.get(data, "delivery.file.outbox") or "runs/outbox")
    outbox.mkdir(parents=True, exist_ok=True)
    target = outbox / f"final-{datetime.now().strftime('%Y-%m-%d')}.md"
    shutil.copyfile(final_path, target)
    print(f"文件渠道：{len(entries)} 条 → {target}（不发送，不记账）")
    return 0


def _latest_final() -> Path | None:
    runs = sorted(
        (ROOT / "runs" / "final").glob("*/final.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return runs[0] if runs else None


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="按配置推送 final.md")
    parser.add_argument("--from-final", default=None, help="默认取最新 runs/final/*/final.md")
    parser.add_argument("--config", default=None, help="默认 config/user.yaml")
    parser.add_argument("--channel", choices=["email", "file", "none"], default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data = user_config.load(args.config)
    problems = user_config.validate(data)
    if problems:
        print("配置有问题，先修 config/user.yaml：", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        return 2
    channel = user_config.resolve_channel(data, args.channel)

    path = Path(args.from_final) if args.from_final else _latest_final()
    if path is None or not path.exists():
        print("错误：找不到 final.md", file=sys.stderr)
        return 2
    entries = _entries(path, args.limit)
    if not entries:
        print("没有任何条目可发", file=sys.stderr)
        return 2
    print(f"来源：{path}　条目 {len(entries)} 条　渠道：{channel}")

    if channel == "none":
        print("delivery.channel=none：不推送。产物已生成。")
        return 0
    if channel == "file":
        if args.dry_run:
            print("file 渠道会复制 final.md 到 outbox；dry-run 不写文件。")
            return 0
        return send_file(entries, data, path)
    if channel == "email":
        code = send_email(entries, data, dry_run=args.dry_run)
        if code == 0 and not args.dry_run:
            committed = push_markdown.commit_ledger_items(
                path, {str(entry.get("item_id") or "") for entry in entries}
            )
            print(f"已落账 {committed} 条")
        return code
    print(f"未知渠道：{channel}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
