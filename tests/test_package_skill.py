"""技能打包：分发包要有清单、一次性配置文档，且不能带个人配置。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools import package_skill


def test_package_has_manifest_first_setup_and_references(tmp_path: Path) -> None:
    out = tmp_path / "skill.skill"
    assert package_skill.main(["--out", str(out)]) == 0
    assert out.exists()
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        root = "personal-information-environment"
        assert f"{root}/SKILL.md" in names
        assert f"{root}/FIRST-SETUP.md" in names
        assert f"{root}/references/adaptation.md" in names
        assert f"{root}/config/user.example.yaml" in names
        manifest = json.loads(archive.read(f"{root}/manifest.json"))
    assert manifest["name"] == "personal-information-environment"
    assert manifest["requires"]["repo"] is True
    paths = {row["path"] for row in manifest["files"]}
    assert "SKILL.md" in paths
    assert "FIRST-SETUP.md" in paths


def test_install_copies_skill_without_personal_config(tmp_path: Path) -> None:
    dest = tmp_path / "installed"
    assert package_skill.main(["--install", "--dest", str(dest)]) == 0
    assert (dest / "SKILL.md").exists()
    assert (dest / "FIRST-SETUP.md").exists()
    assert (dest / "manifest.json").exists()
    assert not (dest / "config" / "user.yaml").exists()
    assert not (dest / "store").exists()
