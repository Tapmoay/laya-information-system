"""一条命令检查整个仓库是否自洽。

    python tools/check.py            # 契约 + 夹具 + 信息源库 + 文档数字 + 测试
    python tools/check.py --no-tests # 只做静态检查（不联网，可离线跑）

存在的理由：文档里的数字（能力数、对象数、夹具数、测试数）最容易悄悄过期，
而"文档说 39 个能力、注册表里只有 38 个"这种错误，人眼很难发现。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKTICK = "\x60"


def _capability_counts() -> tuple[int, int]:
    data = yaml.safe_load((ROOT / "capabilities" / "registry.yaml").read_text(encoding="utf-8"))
    total = len(data["capabilities"])
    implemented = sum(1 for item in data["capabilities"] if item["status"] == "IMPLEMENTED")
    return total, implemented


def _object_count() -> int:
    return len(
        [p for p in (ROOT / "contracts" / "schemas").glob("*.schema.json") if not p.name.startswith("common")]
    )


def _fixture_count() -> int:
    manifest = yaml.safe_load((ROOT / "fixtures" / "contracts" / "manifest.yaml").read_text(encoding="utf-8"))
    return len(manifest["fixtures"])


def _collected_tests() -> int | None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+) tests? collected", result.stdout)
    return int(match.group(1)) if match else None


def _readme_numbers() -> dict[str, int | None]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    numbers: dict[str, int | None] = {}
    match = re.search(r"(\d+) 个能力契约中 (\d+) 个 \x60IMPLEMENTED\x60", text)
    numbers["capabilities"] = int(match.group(1)) if match else None
    numbers["implemented"] = int(match.group(2)) if match else None
    match = re.search(r"(\d+) 个对象契约、(\d+) 个契约夹具、(\d+) 个测试", text)
    numbers["objects"] = int(match.group(1)) if match else None
    numbers["fixtures"] = int(match.group(2)) if match else None
    numbers["tests"] = int(match.group(3)) if match else None
    return numbers


def _registry_doc_numbers() -> dict[str, int | None]:
    text = (ROOT / "docs" / "02-capability-registry.md").read_text(encoding="utf-8")
    numbers: dict[str, int | None] = {}
    total = re.search(r"能力总数：\*\*(\d+)\*\*", text)
    numbers["total"] = int(total.group(1)) if total else None
    states = re.search(
        r"`IMPLEMENTED` (\d+)、`CONTRACTED` (\d+)、`PROPOSED` (\d+)", text
    )
    numbers["implemented"] = int(states.group(1)) if states else None
    numbers["contracted"] = int(states.group(2)) if states else None
    numbers["proposed"] = int(states.group(3)) if states else None
    return numbers


def check_registry_doc() -> list[str]:
    data = yaml.safe_load((ROOT / "capabilities" / "registry.yaml").read_text(encoding="utf-8"))
    actual: dict[str, int] = {}
    for item in data["capabilities"]:
        actual[item["status"]] = actual.get(item["status"], 0) + 1
    stated = _registry_doc_numbers()
    problems: list[str] = []
    if stated["total"] != len(data["capabilities"]):
        problems.append(f"docs/02 写能力总数 {stated['total']}，实际 {len(data['capabilities'])}")
    for key in ("IMPLEMENTED", "CONTRACTED", "PROPOSED"):
        if stated[key.lower()] != actual.get(key, 0):
            problems.append(f"docs/02 写 {key}={stated[key.lower()]}，实际 {actual.get(key, 0)}")
    return problems


def check_test_counts() -> list[str]:
    """所有文档里的"N 个测试"必须一致；README 的那个还要等于实际收集数。"""
    problems: list[str] = []
    patterns = re.compile(r"(\d+) 个测试")
    readme = _readme_numbers().get("tests")
    files = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md"))
    for path in files:
        for match in patterns.finditer(path.read_text(encoding="utf-8")):
            stated = int(match.group(1))
            if readme is not None and stated != readme:
                problems.append(f"{path.name} 写 {stated} 个测试，README 写 {readme}")
    return problems


def check_docs() -> list[str]:
    problems: list[str] = []
    total, implemented = _capability_counts()
    actual = {
        "capabilities": total,
        "implemented": implemented,
        "objects": _object_count(),
        "fixtures": _fixture_count(),
    }
    stated = _readme_numbers()
    for key, value in actual.items():
        if stated.get(key) is None:
            problems.append(f"README 里找不到 {key} 的数字")
        elif stated[key] != value:
            problems.append(f"README 写 {key}={stated[key]}，实际是 {value}")
    return problems


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="仓库自洽性检查")
    parser.add_argument("--no-tests", action="store_true")
    args = parser.parse_args(argv)

    failures: list[str] = []

    print("[1/5] 能力注册表")
    if subprocess.run([sys.executable, "tools/validate_registry.py"], cwd=ROOT).returncode != 0:
        failures.append("注册表校验")

    print("[2/5] 对象契约夹具")
    if subprocess.run([sys.executable, "tools/validate_objects.py", "--all-fixtures"], cwd=ROOT).returncode != 0:
        failures.append("契约夹具")

    print("[3/5] 信息源库")
    if subprocess.run([sys.executable, "tools/source_index.py", "--check"], cwd=ROOT).returncode != 0:
        failures.append("信息源库")

    print("[4/5] 文档数字")
    problems = check_docs() + check_registry_doc() + check_test_counts()
    for problem in problems:
        print(f"  - {problem}")
    if problems:
        failures.append("文档数字")

    if not args.no_tests:
        print("[5/5] 回归测试")
        collected = _collected_tests()
        stated = _readme_numbers().get("tests")
        if collected is not None and stated is not None and collected != stated:
            print(f"  - README 写 tests={stated}，实际收集到 {collected} 个")
            failures.append("测试数量")
        if subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT).returncode != 0:
            failures.append("回归测试")

    print()
    if failures:
        print("失败：" + "、".join(failures))
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
