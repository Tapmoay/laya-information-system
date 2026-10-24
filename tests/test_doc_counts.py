"""文档数字与实际的一致性测试。

README 里写的能力数、对象数、夹具数都是可以机械核对的；
让它们和代码一起漂移，是最容易失去信任的失败方式。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def readme_numbers() -> dict[str, int | None]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    first = re.search(r"(\d+) 个能力契约中 (\d+) 个 \x60IMPLEMENTED\x60", text)
    second = re.search(r"(\d+) 个对象契约、(\d+) 个契约夹具、(\d+) 个测试", text)
    return {
        "capabilities": int(first.group(1)) if first else None,
        "implemented": int(first.group(2)) if first else None,
        "objects": int(second.group(1)) if second else None,
        "fixtures": int(second.group(2)) if second else None,
        "tests": int(second.group(3)) if second else None,
    }


def test_readme_capability_numbers_match_registry() -> None:
    registry = yaml.safe_load((ROOT / "capabilities" / "registry.yaml").read_text(encoding="utf-8"))
    numbers = readme_numbers()
    assert numbers["capabilities"] == len(registry["capabilities"])
    assert numbers["implemented"] == sum(
        1 for item in registry["capabilities"] if item["status"] == "IMPLEMENTED"
    )


def test_registry_doc_status_numbers_match_registry() -> None:
    registry = yaml.safe_load((ROOT / "capabilities" / "registry.yaml").read_text(encoding="utf-8"))
    actual: dict[str, int] = {}
    for item in registry["capabilities"]:
        actual[item["status"]] = actual.get(item["status"], 0) + 1

    text = (ROOT / "docs" / "02-capability-registry.md").read_text(encoding="utf-8")
    total = re.search(r"能力总数：\*\*(\d+)\*\*", text)
    states = re.search(r"IMPLEMENTED. (\d+)、.CONTRACTED. (\d+)、.PROPOSED. (\d+)", text)

    assert total and int(total.group(1)) == len(registry["capabilities"])
    assert states
    assert int(states.group(1)) == actual.get("IMPLEMENTED", 0)
    assert int(states.group(2)) == actual.get("CONTRACTED", 0)
    assert int(states.group(3)) == actual.get("PROPOSED", 0)


def test_all_docs_agree_on_the_test_count() -> None:
    readme = readme_numbers()["tests"]
    assert readme is not None
    for path in (ROOT / "docs").glob("*.md"):
        for match in re.finditer(r"(\d+) 个测试", path.read_text(encoding="utf-8")):
            assert int(match.group(1)) == readme, f"{path.name} 与 README 的测试数不一致"


def test_readme_object_and_fixture_numbers_match_files() -> None:
    objects = len(
        [p for p in (ROOT / "contracts" / "schemas").glob("*.schema.json") if not p.name.startswith("common")]
    )
    manifest = yaml.safe_load((ROOT / "fixtures" / "contracts" / "manifest.yaml").read_text(encoding="utf-8"))
    numbers = readme_numbers()
    assert numbers["objects"] == objects
    assert numbers["fixtures"] == len(manifest["fixtures"])
