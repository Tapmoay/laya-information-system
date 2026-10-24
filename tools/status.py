"""一条命令看清系统当前状态。

    python tools/status.py            # 能力 / 契约 / 政策 / 最近运行 / 观察 / 版本
    python tools/status.py --runs DIR

只读：不写任何文件，不改任何配置。适合开工前先跑一次。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许 python tools/status.py 直接运行
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402


def _capabilities() -> tuple[dict[str, int], int]:
    data = yaml.safe_load((ROOT / "capabilities" / "registry.yaml").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for item in data["capabilities"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    implemented = counts.get("IMPLEMENTED", 0)
    return counts, len(data["capabilities"])


def _contracts() -> tuple[int, int]:
    schemas = list((ROOT / "contracts" / "schemas").glob("*.schema.json"))
    manifest = yaml.safe_load((ROOT / "fixtures" / "contracts" / "manifest.yaml").read_text(encoding="utf-8"))
    return len(schemas), len(manifest["fixtures"])


def _policy() -> dict:
    from policy.loader import load_policy_bundle

    try:
        bundle = load_policy_bundle(ROOT / "information-policy.md", proceed_allowed=True)
    except Exception as exc:  # noqa: BLE001 - 状态命令不该因为政策读不出来就崩
        return {"error": str(exc)}
    file = bundle["files"][0]
    return {
        "version": bundle["policy_version"],
        "changelog_version": file["declared_version_in_changelog"],
        "state": bundle["state"]["status"],
        "conflicts": [item["kind"] for item in bundle["version_conflicts"]],
    }


def _runs(root: Path) -> dict:
    from feedback.detector import detect_observations, load_feedback, load_manifests

    if not root.exists():
        return {"count": 0}
    manifests = load_manifests(root)
    if not manifests:
        return {"count": 0}
    last = manifests[-1]
    feedback = load_feedback(root / "feedback") if (root / "feedback").exists() else []
    observations = detect_observations(manifests, feedback)
    return {
        "count": len(manifests),
        "last_run": last.get("run_id"),
        "last_started": last.get("started_at"),
        "last_status": last.get("run_status"),
        "last_delivery": last.get("delivery_status"),
        "last_counts": last.get("counts"),
        "observations": [item.code for item in observations],
    }


def _versions() -> list[str]:
    root = ROOT / "policy" / "versions"
    if not root.exists():
        return []
    return sorted(path.stem for path in root.glob("*.yaml"))


def _models() -> list[tuple[str, str, str]]:
    path = ROOT / "models" / "registry.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        (item["model_profile_id"], item["implementation_kind"], item["status"])
        for item in data.get("profiles", [])
    ]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="查看系统当前状态")
    parser.add_argument("--runs", default=str(ROOT / "runs"))
    args = parser.parse_args(argv)

    capabilities, total = _capabilities()
    schemas, fixtures = _contracts()
    policy = _policy()
    runs = _runs(Path(args.runs))
    versions = _versions()
    models = _models()

    print("== 能力 ==")
    print(f"  {total} 个：", ", ".join(f"{key}={value}" for key, value in sorted(capabilities.items())))

    print("== 契约 ==")
    print(f"  {schemas} 个 schema，{fixtures} 个夹具")

    print("== 政策 ==")
    if "error" in policy:
        print(f"  读取失败：{policy['error']}")
    else:
        print(
            f"  版本 {policy['version']}（§10 自述 {policy['changelog_version']}）"
            f"  状态 {policy['state']}  冲突 {policy['conflicts'] or '无'}"
        )

    print("== 模型 ==")
    for profile_id, kind, state in models:
        print(f"  {profile_id:<32} {kind:<5} {state}")
    if not models:
        print("  未注册")

    from tools import user_config

    config = user_config.load()
    print("== 个人配置 ==")
    print(
        "  provider={} channel={}".format(
            user_config.get(config, "judge.provider"),
            user_config.get(config, "delivery.channel"),
        )
    )
    try:
        demand = user_config.resolve_demand(config)
        print("  需求：", demand.name)
    except FileNotFoundError:
        print("  需求：缺（先跑 python tools/setup.py）")

    print("== 运行 ==")
    if runs.get("count", 0) == 0:
        print("  还没有运行记录")
    else:
        print(
            f"  最近一轮 {runs['last_run']}（{runs['last_started']}）"
            f" run={runs['last_status']} delivery={runs['last_delivery']}"
        )
        print(f"  计数 {runs['last_counts']}")
        print(f"  共 {runs['count']} 轮；观察 {runs['observations'] or '无'}")

    print("== 规则版本 ==")
    print(f"  {versions or '未激活过任何版本'}")

    print()
    print("下一步：python tools/check.py  →  全仓库自洽检查")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
