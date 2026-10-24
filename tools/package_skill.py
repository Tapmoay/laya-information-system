"""把技能打成可分发的包，或安装到用户级技能目录。

    python tools/package_skill.py                 # 生成 dist/personal-information-environment-<日期>.skill
    python tools/package_skill.py --install       # 安装到 ~/.agents/skills/personal-information-environment
    python tools/package_skill.py --install --force

为什么需要它：
技能要能发给另一个人，就不能只存在于仓库目录里。包里的文件是**手册**，
不带仓库代码、模型权重、个人配置与凭据；对方拿到包 + 仓库后按 FIRST-SETUP.md 配置。

包里包含：
- SKILL.md、PACKAGE.md、FIRST-SETUP.md、references/*.md
- docs/38、docs/39、docs/40（方法与实测）
- config/user.example.yaml（模板，不含真实配置）
- manifest.json（文件清单 + sha256）

不包含：
- tools/、judgment/、ingest/ 等可执行代码（在仓库里）
- config/user.yaml、config/USER.md、store/、runs/
- 任何 key / token / 密码
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "personal-information-environment"
VERSION = "1.0.0"
SKILL_DIR = ROOT / "agent" / "skills" / NAME
DIST_DIR = ROOT / "dist"
DEFAULT_INSTALL = Path.home() / ".agents" / "skills" / NAME

# 额外带上的方法与模板；路径保持与仓库一致，方便正文里的相对引用
EXTRAS = {
    "docs/38-daily-workflow.md": ROOT / "docs" / "38-daily-workflow.md",
    "docs/39-judge-comparison.md": ROOT / "docs" / "39-judge-comparison.md",
    "docs/40-portable-onboarding.md": ROOT / "docs" / "40-portable-onboarding.md",
    "config/user.example.yaml": ROOT / "config" / "user.example.yaml",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect() -> list[tuple[Path, str]]:
    """返回 (源文件, 包内相对路径)。包内路径不含顶层目录名。"""
    if not SKILL_DIR.exists():
        raise SystemExit(f"找不到技能目录：{SKILL_DIR}")
    files: list[tuple[Path, str]] = []
    for path in sorted(SKILL_DIR.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(SKILL_DIR).as_posix()
        if relative.startswith("."):
            continue
        files.append((path, relative))
    for relative, source in EXTRAS.items():
        if not source.exists():
            raise SystemExit(f"缺少要打包的文件：{source}")
        files.append((source, relative))
    return files


def manifest(files: list[tuple[Path, str]]) -> dict:
    return {
        "name": NAME,
        "version": VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "requires": {
            "repo": True,
            "python": ">=3.12",
            "note": "包只是手册；执行需要仓库 tools/ 与依赖。",
        },
        "files": [{"path": relative, "sha256": _sha256(source)} for source, relative in files],
    }


def _remove_existing(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    is_junction = getattr(path, "is_junction", lambda: False)
    if is_junction():
        path.rmdir()
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def install(dest: Path | None = None, *, force: bool = False) -> Path:
    target = Path(dest) if dest else DEFAULT_INSTALL
    # 不能用 resolve() 比较：目标可能是指向仓库的 junction，resolve 后会等于源目录，
    # 于是"安装副本"被误判成"覆盖源仓库"。这里比较字面绝对路径，junction 由 _remove_existing 处理。
    if target.absolute() == SKILL_DIR.absolute():
        raise SystemExit("目标就是源目录，拒绝覆盖")
    if target.exists() or target.is_symlink():
        if not force:
            raise SystemExit(
                f"目标已存在：{target}\n确认要覆盖就加 --force（会删掉旧副本，源仓库不受影响）"
            )
        _remove_existing(target)
    target.mkdir(parents=True, exist_ok=True)
    files = collect()
    for source, relative in files:
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, out)
    (target / "manifest.json").write_text(
        json.dumps(manifest(files), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def build(out: Path | None = None) -> Path:
    files = collect()
    target = Path(out) if out else DIST_DIR / f"{NAME}-{datetime.now().strftime('%Y-%m-%d')}.skill"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, relative in files:
            archive.write(source, f"{NAME}/{relative}")
        archive.writestr(
            f"{NAME}/manifest.json",
            json.dumps(manifest(files), ensure_ascii=False, indent=2) + "\n",
        )
    return target


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="打包 / 安装个人信息环境技能")
    parser.add_argument("--out", default=None, help="输出 .skill 路径")
    parser.add_argument("--install", action="store_true", help="安装到用户级技能目录")
    parser.add_argument("--dest", default=None, help="安装目标，默认 ~/.agents/skills/<name>")
    parser.add_argument("--force", action="store_true", help="覆盖已有安装")
    args = parser.parse_args(argv)

    if args.install:
        target = install(Path(args.dest) if args.dest else None, force=args.force)
        print(f"已安装：{target}")
        print("下一步：让对方读 FIRST-SETUP.md，配置完成后删除它。")
        return 0

    target = build(Path(args.out) if args.out else None)
    print(f"已生成：{target}")
    print(f"大小：{target.stat().st_size / 1024:.1f} KB")
    print("内容：SKILL.md / FIRST-SETUP.md / PACKAGE.md / references / docs / config/user.example.yaml")
    print("不包含：tools、模型权重、config/user.yaml、store/、任何凭据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
