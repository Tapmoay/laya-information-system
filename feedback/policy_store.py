"""策略版本存储。

每次激活都留下一个不可变版本文件 + 一行索引；回滚 = 读回旧版本。
不做数据库：文件即可，可 diff、可 grep、可直接看。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


class PolicyStoreError(ValueError):
    pass


class PolicyStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, version: str) -> Path:
        return self.root / f"{version}.yaml"

    def _index(self) -> Path:
        return self.root / "index.jsonl"

    def save(self, policy: dict[str, Any], *, saved_at: datetime, note: str = "") -> Path:
        version = policy.get("policy_version")
        if not version:
            raise PolicyStoreError("策略缺少 policy_version")
        target = self._path(version)
        if target.exists():
            raise PolicyStoreError(f"版本 {version} 已存在，不允许覆盖")
        self.root.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(policy, allow_unicode=True, sort_keys=False)
        target.write_text(text, encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._index().open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"version": version, "saved_at": saved_at.isoformat(timespec="seconds"), "sha256": digest, "note": note},
                    ensure_ascii=False,
                )
                + "\n"
            )
        return target

    def load(self, version: str) -> dict[str, Any]:
        path = self._path(version)
        if not path.exists():
            raise PolicyStoreError(f"版本 {version} 不存在")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def versions(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(path.stem for path in self.root.glob("*.yaml"))


class RecordStore:
    """反馈 / 结果 / 提案的 JSONL 存储。只追加，不修改。"""

    KINDS = ("feedback", "outcomes", "proposals", "activations")

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, kind: str) -> Path:
        if kind not in self.KINDS:
            raise ValueError(f"未知记录类型：{kind}")
        return self.root / f"{kind}.jsonl"

    def append(self, kind: str, record: dict[str, Any]) -> None:
        path = self._path(kind)
        identifier = (
            record.get("feedback_id")
            or record.get("outcome_id")
            or record.get("proposal_id")
            or record.get("activation_id")
        )
        if not identifier:
            raise ValueError("记录缺少主键")
        existing = {item.get("_id") for item in self.read(kind)}
        if identifier in existing:
            raise ValueError(f"{kind} 里已存在 {identifier}")
        self.root.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"_id": identifier, **record}, ensure_ascii=False) + "\n")

    def read(self, kind: str) -> list[dict[str, Any]]:
        path = self._path(kind)
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records
