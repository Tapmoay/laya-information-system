"""阶段检查点。

只复用输入与配置版本一致的成功产物（v0.3 §2.3）。检查点的 key 由调用方提供的
规范化输入计算，本模块不解释内容。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CheckpointStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, stage: str) -> Path:
        return self.root / f"{stage}.json"

    def load(self, stage: str, key: str) -> Any | None:
        path = self._path(stage)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if payload.get("key") != key:
            return None
        return payload.get("payload")

    def save(self, stage: str, key: str, payload: Any, created_at: datetime) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(stage)
        path.write_text(
            json.dumps(
                {
                    "stage": stage,
                    "key": key,
                    "created_at": created_at.isoformat(timespec="seconds"),
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return path
