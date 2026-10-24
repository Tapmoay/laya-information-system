"""一条命令跑完 M1 / M2 / M3 的验收，并把每一步的产物路径打印出来。

    python tools/acceptance.py --out runs/acceptance

它不替代回归测试，而是给"这套东西现在到底能不能跑"一个可以当场看的结果。
任何一步失败，退出码为 1。
"""

from __future__ import annotations

import argparse
import http.server
import json
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from delivery.push import PushConfig  # noqa: E402
from feedback import approve_cli, diff_cli  # noqa: E402
from feedback.activation import Approval, activate, rollback  # noqa: E402
from feedback.detector import detect_observations, load_feedback, load_manifests  # noqa: E402
from feedback.policy_diff import evaluate_diff  # noqa: E402
from feedback.policy_store import PolicyStore, RecordStore  # noqa: E402
from feedback.proposal import build_proposal  # noqa: E402
from ingest.fetch import FetchPolicy  # noqa: E402
from runtime import scheduled_entry  # noqa: E402
from runtime.live_run import LiveRunConfig, run_live  # noqa: E402
from runtime.offline_run import load_case, run_case  # noqa: E402
from selection.engine import load_policy  # noqa: E402

NOW = datetime.fromisoformat("2026-10-18T07:00:00+08:00")
STAMP = NOW.strftime("%Y%m%d%H%M")


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    artifact: str = ""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "fixtures" / "html"), **kwargs)

    def log_message(self, *args):
        return


def _serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def step_m1(root: Path) -> Step:
    out = root / "m1"
    outcome = run_case(ROOT / "fixtures" / "m1" / "basic", out_dir=out, run_id=f"acc-m1-{STAMP}", now=NOW)
    ok = outcome["manifest"]["run_status"] == "SUCCESS" and bool(outcome["delivery"]["items"])
    return Step("M1 离线链路", ok, f"交付 {len(outcome['delivery']['items'])} 条", str(out / "rendered-brief.json"))


def step_m2(root: Path, url: str) -> Step:
    out = root / "m2"
    config = LiveRunConfig(
        urls=[f"{url}/article.html", f"{url}/chinese.html"],
        demand=json.loads((ROOT / "fixtures" / "m1" / "basic" / "demand-snapshot.json").read_text(encoding="utf-8")),
        policy_config=json.loads((ROOT / "fixtures" / "m1" / "basic" / "policy-config.json").read_text(encoding="utf-8")),
        fetch_policy=FetchPolicy(allowed_hosts=("127.0.0.1",), max_attempts=2, timeout_seconds=5),
        push=PushConfig(channel="file", target=str(out / "outbox")),
        allow_unvalidated=True,
    )
    outcome = run_live(config, run_id=f"acc-m2-{STAMP}", out_dir=out, now=NOW)
    manifest = outcome["manifest"]
    ok = manifest.get("run_status") == "SUCCESS" and manifest.get("delivery_status") == "SUCCESS"
    return Step(
        "M2 在线链路 + 推送",
        ok,
        f"抓取 {manifest.get('counts', {}).get('candidates', 0)} 个候选，推送 {manifest.get('delivery_status')}",
        str(out / "run-manifest.json"),
    )


def step_m3(root: Path) -> list[Step]:
    out = root / "m3"
    store = RecordStore(out / "store")
    steps: list[Step] = []

    store.append(
        "feedback",
        {
            "_id": "fb-acc-1",
            "feedback_id": "fb-acc-1",
            "candidate_id": "cand-m1-0001",
            "kind": "USER_STATED",
            "captured_at": NOW.isoformat(timespec="seconds"),
            "source": "USER_MESSAGE",
            "statement": {"text": "这个方向想跟进", "language": "zh-Hans"},
            "sentiment": "LIKE",
            "delivery_id": None,
            "contract_version": "1.0",
        },
    )

    manifests = load_manifests(root / "m1")
    feedback = load_feedback(out / "store")
    observations = detect_observations(manifests * 3, feedback)
    steps.append(
        Step(
            "M3 反馈记录 + 检测",
            True,
            f"反馈 {len(feedback)} 条，观察 {[item.code for item in observations] or '无'}",
            str(out / "store" / "feedback.jsonl"),
        )
    )

    proposal = build_proposal(
        load_policy(),
        proposal_id="prop-acc-1",
        rule_id="RULE-ADJACENT-CHANNEL",
        rationale="验收：邻接内容长期占核心通道",
        trigger_observation="最近三轮核心交付都是邻接相关内容",
        evidence_refs=["runs/acceptance/m1/run-manifest.json"],
        changes=[{"pointer": "/channels/ADJACENT", "before": "CORE", "after": "EXPLORATION"}],
        expected_effect="核心通道只保留直接相关内容",
        side_effects=["邻接内容失去核心位"],
        review_condition="两周后检查核心通道",
        rollback_condition="核心通道连续五轮无交付",
        created_at=NOW,
    )
    proposal_path = out / "proposal.json"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")

    diff_code = diff_cli.main([
        "--proposal", str(proposal_path),
        "--case", str(ROOT / "fixtures" / "m1" / "basic"),
        "--out", str(out / "diff.json"),
        "--at", NOW.isoformat(),
    ])
    steps.append(Step("M3 规则对照", diff_code == 0, "生成对照报告", str(out / "diff.json")))

    approve_code = approve_cli.main([
        "--proposal", str(proposal_path),
        "--diff", str(out / "diff.json"),
        "--store", str(out / "versions"),
        "--records", str(out / "store"),
        "--approver", "验收脚本",
        "--confirm", "RULE-ADJACENT-CHANNEL",
        "--at", NOW.isoformat(),
    ])
    versions = PolicyStore(out / "versions").versions()
    steps.append(Step("M3 人工批准 + 版本", approve_code == 0 and len(versions) >= 2, f"版本 {versions}", str(out / "versions")))

    activation = RecordStore(out / "store").read("activations")
    restored = None
    if activation:
        stored = PolicyStore(out / "versions")
        restored = rollback(activation[0], stored)
    steps.append(
        Step(
            "M3 回滚",
            bool(restored) and restored.get("channels", {}).get("ADJACENT") == "CORE",
            "回滚到基线策略",
            str(out / "store" / "activations.jsonl"),
        )
    )
    return steps


def step_scheduler(root: Path, url: str) -> Step:
    out = root / "sched"
    sources = {
        "sources": {
            "allowed_hosts": ["127.0.0.1"],
            "timeout_seconds": 5,
            "max_attempts": 1,
            "urls": [f"{url}/article.html"],
        },
        "run": {
            "demand_path": "fixtures/m1/basic/demand-snapshot.json",
            "policy_config_path": "fixtures/m1/basic/policy-config.json",
            "allow_unvalidated": True,
        },
    }
    sources_path = out / "sources.yaml"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(yaml.safe_dump(sources, allow_unicode=True), encoding="utf-8")

    schedule = {
        "schedule": {
            "enabled": True,
            "timezone": "Asia/Shanghai",
            "calendar_rule": "0 9 * * *",
            "misfire_grace_seconds": 3600,
            "misfire_policy": "RUN_ONCE",
            "max_concurrent_runs": 1,
        }
    }
    schedule_path = out / "schedule.yaml"
    schedule_path.write_text(yaml.safe_dump(schedule, allow_unicode=True), encoding="utf-8")

    first = scheduled_entry.main([
        "--config", str(schedule_path), "--mode", "live", "--sources", str(sources_path),
        "--out", str(out / "runs"), "--now", "2026-10-18T09:00:00+08:00",
    ])
    second = scheduled_entry.main([
        "--config", str(schedule_path), "--mode", "live", "--sources", str(sources_path),
        "--out", str(out / "runs"), "--now", "2026-10-18T09:05:00+08:00",
    ])
    manifests = list((out / "runs").rglob("run-manifest.json"))
    ok = first == 0 and second == 0 and len(manifests) == 1
    return Step("定时：首轮运行 + 次轮不重复", ok, f"清单 {len(manifests)} 份", str(out / "runs"))


def run_all(root: Path) -> list[Step]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    server, url = _serve()
    steps: list[Step] = []
    try:
        steps.append(step_m1(root))
        steps.append(step_m2(root, url))
        steps.extend(step_m3(root))
        steps.append(step_scheduler(root, url))
    finally:
        server.shutdown()
    return steps


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="端到端验收")
    parser.add_argument("--out", default=str(ROOT / "runs" / "acceptance"))
    args = parser.parse_args(argv)

    root = Path(args.out)
    if root.exists():
        shutil.rmtree(root)

    steps = run_all(root)

    print("== 验收 ==")
    for step in steps:
        print(f"  [{'通过' if step.ok else '失败'}] {step.name}：{step.detail}")
        if step.artifact:
            print(f"          {step.artifact}")

    failed = [step for step in steps if not step.ok]
    print()
    if failed:
        print(f"失败 {len(failed)} 项")
        return 1
    print(f"全部通过（{len(steps)} 项）。产物：{root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
