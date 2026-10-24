"""推送（CAP-DEL-03）与推送状态（CAP-DEL-04）。

M1/M2 只做本地文件通道：不需要凭据、可以在测试里完整验证幂等。
幂等键由"交付引用 + 简报正文 + 通道"算出：同一份内容重试不会产生第二份推送。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class PushConfig:
    channel: str = "none"
    target: str | None = None
    max_attempts: int = 3

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "PushConfig":
        if "delivery" in data:
            data = data["delivery"]
        allowed = {"channel", "target", "max_attempts"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"未知的推送配置项：{sorted(unknown)}")
        channel = str(data.get("channel", "none"))
        if channel not in {"none", "file"}:
            raise ValueError(f"不支持的通道：{channel}")
        target = data.get("target")
        if channel == "file" and not target:
            raise ValueError("file 通道必须配置 target")
        attempts = int(data.get("max_attempts", 3))
        if not 1 <= attempts <= 10:
            raise ValueError("max_attempts 必须在 1..10")
        return cls(channel=channel, target=str(target) if target else None, max_attempts=attempts)


class PushFailed(RuntimeError):
    pass


def idempotency_key(brief: dict[str, Any], channel: str) -> str:
    """幂等键只覆盖"用户实际会看到的内容"，不覆盖渲染时间。

    简报正文里带生成时间戳；如果用正文做键，同一份交付每次重跑都会变成"新内容"，
    幂等就失效了。所以键取交付引用 + 条目列表（标题、链接、动作、警示都在条目里）。
    """
    payload = json.dumps(
        {"delivery": brief["delivery_ref"], "items": brief["items"], "channel": channel},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ledger_path(target: Path) -> Path:
    return target / ".push-ledger.jsonl"


def _read_ledger(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        entries[record.get("key", "")] = record
    return entries


def _base_status(brief: dict[str, Any], run_id: str, channel: str, key: str) -> dict[str, Any]:
    return {
        "delivery_status_id": f"dstat-{brief['brief_id']}",
        "run_id": run_id,
        "delivery_ref": brief["delivery_ref"],
        "channel": channel,
        "idempotency_key": key,
        "status": "NOT_ATTEMPTED",
        "attempts": 0,
        "pushed_at": None,
        "artifact_ref": None,
        "error_ref": None,
        "contract_version": "1.0",
    }


def push_brief(
    brief: dict[str, Any],
    config: PushConfig,
    run_id: str,
    now: datetime,
) -> dict[str, Any]:
    if config.channel == "none":
        key = idempotency_key(brief, "NONE")
        return _base_status(brief, run_id, "NONE", key)

    if config.channel != "file":
        raise PushFailed(f"不支持的通道：{config.channel}")

    assert config.target is not None
    target = Path(config.target)
    key = idempotency_key(brief, "FILE")
    status = _base_status(brief, run_id, "FILE", key)

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        status.update(status="FAILED", attempts=1, error_ref=f"trace/{run_id}/push/error")
        raise PushFailed(str(exc)) from exc

    artifact = target / f"{brief['brief_id']}.md"
    ledger = _read_ledger(_ledger_path(target))

    existing = ledger.get(key)
    if existing and artifact.exists():
        status.update(
            status="ALREADY_SENT",
            attempts=0,
            pushed_at=existing.get("pushed_at"),
            artifact_ref=str(artifact),
        )
        return status

    last_error: Exception | None = None
    for attempt in range(1, config.max_attempts + 1):
        status["attempts"] = attempt
        try:
            artifact.write_text(brief["body_markdown"], encoding="utf-8")
        except OSError as exc:
            last_error = exc
            continue
        with _ledger_path(target).open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "key": key,
                        "brief_id": brief["brief_id"],
                        "run_id": run_id,
                        "pushed_at": now.isoformat(timespec="seconds"),
                        "artifact": str(artifact),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        status.update(
            status="SUCCESS",
            pushed_at=now.isoformat(timespec="seconds"),
            artifact_ref=str(artifact),
        )
        return status

    status.update(status="FAILED", error_ref=f"trace/{run_id}/push/error")
    raise PushFailed(str(last_error))
