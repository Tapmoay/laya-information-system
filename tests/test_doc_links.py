"""文档引用完整性：链接和路径必须指向真实存在的文件。

文档最隐蔽的失效方式不是写错，而是指向一个已经不存在的文件——
读者点开才发现，而写的人永远不知道。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
# 这两个是本机运行时配置，不进 git；文档里允许引用它们的名字。
RUNTIME_ONLY = {"config/user.yaml", "config/USER.md"}

LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
PATH = re.compile(r"\x60([A-Za-z0-9_][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]+\.(?:py|md|yaml|yml|json|toml))\x60")


def test_markdown_links_point_to_existing_files() -> None:
    missing: list[str] = []
    for doc in DOCS:
        for match in LINK.finditer(doc.read_text(encoding="utf-8")):
            target = match.group(2).split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (doc.parent / target).resolve().exists():
                missing.append(f"{doc.name} -> {target}")
    assert not missing, missing


def test_backticked_paths_point_to_existing_files() -> None:
    missing: list[str] = []
    for doc in DOCS:
        for match in PATH.finditer(doc.read_text(encoding="utf-8")):
            target = match.group(1)
            if target in RUNTIME_ONLY:
                continue
            if target.startswith(("runs/", "store/", "trace/", "out/")):
                continue
            if not (ROOT / target).exists():
                missing.append(f"{doc.name} -> {target}")
    assert not missing, missing
