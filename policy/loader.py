"""读取政策文档并产出 PolicyBundle（CAP-POL-01）。

系统第一次真正"读"政策，而不是靠夹具假装读过。
它只做确定性的事：算哈希、抽自述版本、发现版本冲突、给出状态。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.validate_objects import validate

VERSION_RE = re.compile(r"当前版本[：:]\s*\x60?(v[0-9]+\.[0-9]+)\x60?")
HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)

ROLES = {"POLICY", "GOAL", "TASKS", "CONSTRAINTS", "MACHINE_CONFIG"}


class PolicyLoadError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _split_sections(text: str) -> tuple[str, dict[str, str]]:
    """返回（首个二级标题之前的头部, {标题首行: 段落正文}）。"""
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return text, {}
    front = text[: matches[0].start()]
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = text[match.start(): text.find("\n", match.start())].strip()
        sections[heading] = text[match.start():end]
    return front, sections


def describe_file(path: Path, role: str) -> dict[str, Any]:
    if role not in ROLES:
        raise PolicyLoadError(f"未知文件角色：{role}")
    if not path.exists():
        raise PolicyLoadError(f"文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    front, sections = _split_sections(text)
    declared = VERSION_RE.search(front)
    changelog = next(
        (body for heading, body in sections.items() if heading.startswith("## 10")),
        "",
    )
    declared_changelog = VERSION_RE.search(changelog)
    return {
        "role": role,
        "path": str(path).replace("\\", "/"),
        "sha256": _sha256(path),
        "byte_length": path.stat().st_size,
        "declared_version": declared.group(1) if declared else None,
        "declared_version_in_changelog": declared_changelog.group(1) if declared_changelog else None,
    }


def load_policy_bundle(
    policy_path: Path | str,
    *,
    extra_files: list[tuple[str, Path | str]] | None = None,
    expected_version: str | None = None,
    proceed_allowed: bool = False,
    bundle_id: str = "bundle-local-0001",
    compiled_at: datetime | None = None,
) -> dict[str, Any]:
    policy_path = Path(policy_path)
    compiled_at = compiled_at or datetime.now().astimezone()

    files = [describe_file(policy_path, "POLICY")]
    for role, path in extra_files or []:
        files.append(describe_file(Path(path), role))

    policy_file = files[0]
    conflicts: list[dict[str, Any]] = []
    if (
        policy_file["declared_version"]
        and policy_file["declared_version_in_changelog"]
        and policy_file["declared_version"] != policy_file["declared_version_in_changelog"]
    ):
        conflicts.append(
            {
                "file": policy_file["path"],
                "kind": "SELF_DECLARED_MISMATCH",
                "values": [policy_file["declared_version"], policy_file["declared_version_in_changelog"]],
            }
        )
    if expected_version and expected_version != policy_file["declared_version"]:
        conflicts.append(
            {
                "file": policy_file["path"],
                "kind": "EXPECTED_VERSION_MISMATCH",
                "values": [str(expected_version), str(policy_file["declared_version"])],
            }
        )

    version = policy_file["declared_version"]
    if not version:
        raise PolicyLoadError(f"在 {policy_path} 里找不到自述版本")

    if not conflicts:
        state = {"status": "OK"}
    elif proceed_allowed:
        state = {"status": "PARTIAL", "reason_code": "VERSION_CONFLICT", "detail_ref": "docs/01-open-conflicts.md#C-01"}
    else:
        state = {"status": "INVALID", "reason_code": "VERSION_CONFLICT"}

    bundle = {
        "bundle_id": bundle_id,
        "compiled_at": compiled_at.isoformat(timespec="seconds"),
        "files": files,
        "policy_version": version,
        "version_conflicts": conflicts,
        "proceed_allowed": proceed_allowed,
        "state": state,
        "contract_version": "1.0",
    }
    problems = validate("policy-bundle", bundle)
    if problems:
        raise PolicyLoadError("政策包违反契约：" + "; ".join(problems))
    return bundle
