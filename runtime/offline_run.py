"""离线单轮入口（WP-R5 + WP-R7 的 M1 部分）。

流程：拿锁 → PRECHECK → SELECTION → DELIVERY_BUILD → PUSH(跳过) → 写清单 → 释放锁。

M1 明确不做：不抓取、不调用模型、不推送、不翻译、不挂调度。
但每一次运行（包括失败的运行）都会留下 RunManifest。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from delivery.builder import build_delivery
from delivery.push import PushConfig, push_brief
from delivery.renderer import render_brief
from runtime.checkpoints import canonical_hash
from runtime.manifest import build_manifest
from runtime.run_manager import RunContext, RunManager, StageOutcome, StageSpec
from selection.engine import load_policy, select
from tools.validate_objects import validate

CONTRACT_VERSIONS = {
    "POLICY-BUNDLE": "1.0",
    "POLICY-CONFIG": "1.0",
    "DEMAND-SNAPSHOT": "1.0",
    "CANDIDATE-RECORD": "1.0",
    "MEASUREMENT-RECORD": "1.0",
    "SELECTION-DECISION": "1.0",
    "DELIVERY-OBJECT": "1.0",
    "RUN-MANIFEST": "1.0",
}


@dataclass
class CaseInput:
    case_dir: Path
    bundle: dict[str, Any]
    config: dict[str, Any]
    demand: dict[str, Any]
    candidates: list[dict[str, Any]]
    measurements: list[dict[str, Any]]


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_case(case_dir: Path | str) -> CaseInput:
    case_dir = Path(case_dir)
    candidates = [_read(path) for path in sorted((case_dir / "candidates").glob("*.json"))]
    measurements = [_read(path) for path in sorted((case_dir / "measurements").glob("*.json"))]
    return CaseInput(
        case_dir=case_dir,
        bundle=_read(case_dir / "policy-bundle.json"),
        config=_read(case_dir / "policy-config.json"),
        demand=_read(case_dir / "demand-snapshot.json"),
        candidates=candidates,
        measurements=measurements,
    )


def case_key(case_dir: Path | str, extra: Any = None) -> str:
    """输入指纹：按文件内容算，不看时间戳。任何输入改动都会让检查点失效。"""
    case_dir = Path(case_dir)
    files = sorted(case_dir.rglob("*.json"))
    payload = [(str(path.relative_to(case_dir)), path.read_text(encoding="utf-8")) for path in files]
    policy = load_policy()
    return canonical_hash(
        {
            "files": payload,
            "selector_version": policy["selector_version"],
            "selector_policy_version": policy["policy_version"],
            "extra": extra,
        }
    )


def _require_valid(kind: str, payload: dict[str, Any], label: str) -> None:
    errors = validate(kind, payload)
    if errors:
        raise ValueError(f"{label} 违反 {kind} 契约：" + "; ".join(errors))


def _stage(name: str, status: str, at: datetime) -> dict[str, Any]:
    stamp = at.isoformat(timespec="seconds") if status == "OK" else None
    return {"stage": name, "status": status, "started_at": stamp, "finished_at": stamp, "error_ref": None}


def run_case(
    case_dir: Path | str,
    out_dir: Path | str | None = None,
    run_id: str = "run-offline-0001",
    now: datetime | None = None,
    trigger: str = "TEST",
    reuse_checkpoints: bool = True,
    raise_on_failure: bool = False,
    push_config: PushConfig | dict[str, Any] | None = None,
    policy_doc: Path | str | None = None,
    proceed_on_policy_conflict: bool = False,
) -> dict[str, Any]:
    case_dir = Path(case_dir)
    out_path = Path(out_dir) if out_dir is not None else None
    now = now or datetime.now().astimezone()

    if push_config is None:
        # 没有显式推送配置时，重试次数取政策编译出来的 run_limits
        _base_limits = (load_case(case_dir).config.get("run_limits") or {}) if policy_doc is None else {}
        _retry = _base_limits.get("push_retry_limit")
        push_settings = PushConfig(max_attempts=int(_retry)) if _retry else PushConfig()
    elif isinstance(push_config, PushConfig):
        push_settings = push_config
    else:
        push_settings = PushConfig.from_mapping(push_config)

    lock_path = (out_path / ".run.lock") if out_path else (Path(tempfile.mkdtemp(prefix="laya-lock-")) / "run.lock")
    checkpoint_root = (out_path / "checkpoints") if (out_path and reuse_checkpoints) else None

    # 时间预算来自政策配置的 run_limits，不是命令行开关
    if policy_doc is None:
        _limits = (load_case(case_dir).config.get("run_limits") or {})
    else:
        _limits = {}
    _budget = _limits.get("max_duration_minutes")
    manager = RunManager(
        lock_path=lock_path,
        run_id=run_id,
        clock=lambda: now,
        checkpoint_root=checkpoint_root,
        max_duration_seconds=(float(_budget) * 60 if _budget else None),
    )

    def precheck(context: RunContext) -> dict[str, Any]:
        case = load_case(case_dir)
        if policy_doc is not None:
            from policy.compiler import compile_policy_config
            from policy.loader import load_policy_bundle

            bundle = load_policy_bundle(
                policy_doc,
                proceed_allowed=proceed_on_policy_conflict,
                bundle_id=f"bundle-{run_id}",
                compiled_at=now,
            )
            if bundle["state"]["status"] == "INVALID":
                raise ValueError(
                    "政策包状态为 INVALID（"
                    + str(bundle["state"].get("reason_code"))
                    + "）：自述版本冲突且未获准继续"
                )
            config = compile_policy_config(bundle, config_id=f"config-{run_id}", compiled_at=now)
        else:
            bundle, config = case.bundle, case.config

        _require_valid("policy-bundle", bundle, "policy-bundle")
        _require_valid("policy-config", config, "policy-config")
        _require_valid("demand-snapshot", case.demand, "demand-snapshot.json")
        for candidate in case.candidates:
            _require_valid("candidate-record", candidate, candidate["candidate_id"])
        for measurement in case.measurements:
            _require_valid("measurement-record", measurement, measurement["measurement_id"])
        return {
            "bundle": bundle,
            "config": config,
            "demand": case.demand,
            "candidates": case.candidates,
            "measurements": case.measurements,
            "compiled_from_policy": policy_doc is not None,
        }

    def selection(context: RunContext) -> dict[str, Any]:
        pre = context.get("PRECHECK")
        result = select(
            candidates=pre["candidates"],
            measurements=pre["measurements"],
            demand=pre["demand"],
            policy_config=pre["config"],
            policy=load_policy(),
            decided_at=now,
        )
        for decision in result.decisions:
            _require_valid("selection-decision", decision, decision["selection_id"])
        return {
            "decisions": result.decisions,
            "delivery_order": result.delivery_order,
            "constraint_checks": result.constraint_checks,
            "actions": result.actions,
        }

    def delivery(context: RunContext) -> dict[str, Any]:
        pre = context.get("PRECHECK")
        sel = context.get("SELECTION")
        payload = build_delivery(
            delivery_id=f"del-{run_id}",
            built_at=now,
            candidates_by_id={item["candidate_id"]: item for item in pre["candidates"]},
            decisions=sel["decisions"],
            delivery_order=sel["delivery_order"],
            actions=sel["actions"],
            policy_config=pre["config"],
        )
        _require_valid("delivery-object", payload, payload["delivery_id"])
        brief = render_brief(
            payload, now, decisions=sel["decisions"], measurements=pre["measurements"]
        )
        _require_valid("rendered-brief", brief, brief["brief_id"])
        return {"delivery": payload, "brief": brief}

    def push(context: RunContext) -> dict[str, Any]:
        brief = context.get("DELIVERY_BUILD")["brief"]
        status = push_brief(brief, push_settings, run_id, now)
        _require_valid("delivery-status", status, status["delivery_status_id"])
        return {"status": status}

    stages = [
        StageSpec("PRECHECK", precheck),
        StageSpec("SELECTION", selection, cacheable=True),
        StageSpec("DELIVERY_BUILD", delivery, cacheable=True),
        StageSpec(
            "PUSH",
            push,
            skip=push_settings.channel == "none",
            partial_on_error=True,
            status_from_payload=lambda payload: "PARTIAL"
            if payload.get("status", {}).get("status") == "FAILED"
            else "OK",
        ),
    ]

    artifacts: list[str] = []
    manifest: dict[str, Any] = {}

    def on_complete(outcome) -> None:
        pre = outcome.payloads.get("PRECHECK")
        sel = outcome.payloads.get("SELECTION")
        delivered = outcome.payloads.get("DELIVERY_BUILD")

        push_payload = outcome.payloads.get("PUSH") or {}
        push_stage = next((stage for stage in outcome.stages if stage.name == "PUSH"), None)
        if push_stage is None or push_stage.status == "SKIPPED":
            delivery_status = "NOT_ATTEMPTED"
        elif push_stage.status == "PARTIAL":
            delivery_status = "FAILED"
        else:
            raw = push_payload.get("status", {}).get("status", "NOT_ATTEMPTED")
            delivery_status = "SUCCESS" if raw in {"SUCCESS", "ALREADY_SENT"} else raw

        stage_entries = [item.as_manifest() for item in outcome.stages]
        stage_entries.append(_stage("PERSIST_RUN_REPORT", "OK", now))
        stage_entries.append(_stage("RELEASE_RESOURCES", "SKIPPED", now))

        built = build_manifest(
            run_id=run_id,
            trigger=trigger,
            started_at=now,
            finished_at=now,
            timezone=(pre or {}).get("bundle", {}).get("timezone") or "Asia/Shanghai",
            policy_version=(pre or {}).get("bundle", {}).get("policy_version", "UNKNOWN"),
            contract_versions=CONTRACT_VERSIONS,
            model_revisions=[],
            stages=stage_entries,
            delivery_status=delivery_status,
            counts={
                "candidates": len((pre or {}).get("candidates", [])),
                "measurements": len((pre or {}).get("measurements", [])),
                "selected": len((sel or {}).get("delivery_order", [])),
                "delivered": len((delivered or {}).get("delivery", {}).get("items", []))
                if delivery_status == "SUCCESS"
                else 0,
            },
            artifacts=artifacts,
        )

        if out_path is not None:
            out_path.mkdir(parents=True, exist_ok=True)
            if (pre or {}).get("compiled_from_policy"):
                (out_path / "policy-bundle.json").write_text(
                    json.dumps(pre["bundle"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                artifacts.append("policy-bundle.json")
                (out_path / "policy-config.json").write_text(
                    json.dumps(pre["config"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                artifacts.append("policy-config.json")
            if sel is not None:
                (out_path / "selection-decisions.json").write_text(
                    json.dumps(sel["decisions"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                artifacts.append("selection-decisions.json")
            if delivered is not None:
                (out_path / "delivery-object.json").write_text(
                    json.dumps(delivered["delivery"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                artifacts.append("delivery-object.json")
                (out_path / "rendered-brief.json").write_text(
                    json.dumps(delivered["brief"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                artifacts.append("rendered-brief.json")
            if push_payload.get("status") is not None:
                (out_path / "delivery-status.json").write_text(
                    json.dumps(push_payload["status"], ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                artifacts.append("delivery-status.json")
            problems = [
                stage
                for stage in outcome.stages
                if stage.status in {"FAILED", "PARTIAL"} and stage.error
            ]
            if problems:
                (out_path / "errors.json").write_text(
                    json.dumps(
                        {stage.name: stage.error for stage in problems},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                artifacts.append("errors.json")
            built["artifacts"] = artifacts + ["run-manifest.json"]
            (out_path / "run-manifest.json").write_text(
                json.dumps(built, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        _require_valid("run-manifest", built, run_id)
        manifest.update(built)
        outcome.stages.append(StageOutcome("PERSIST_RUN_REPORT", "OK"))
        outcome.stages.append(StageOutcome("RELEASE_RESOURCES", "SKIPPED"))

    extra = None
    if policy_doc is not None:
        from pathlib import Path as _Path

        extra = _Path(policy_doc).read_text(encoding="utf-8")
    outcome = manager.execute(stages, case_key(case_dir, extra), on_complete)

    result = {
        "run_id": run_id,
        "decisions": outcome.payloads.get("SELECTION", {}).get("decisions", []),
        "delivery": outcome.payloads.get("DELIVERY_BUILD", {}).get("delivery"),
        "brief": outcome.payloads.get("DELIVERY_BUILD", {}).get("brief"),
        "push_status": (outcome.payloads.get("PUSH") or {}).get("status"),
        "manifest": manifest,
        "constraint_checks": outcome.payloads.get("SELECTION", {}).get("constraint_checks", []),
        "outcome": outcome,
        "failed_stage": outcome.failed_stage,
        "error": outcome.error,
    }
    if raise_on_failure and outcome.failed_stage:
        raise RuntimeError(f"运行在 {outcome.failed_stage} 阶段失败：{outcome.error}")
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="离线跑一轮（M1）")
    parser.add_argument("--case", required=True, help="夹具目录，例如 fixtures/m1/basic")
    parser.add_argument("--out", default=None, help="输出目录")
    parser.add_argument("--run-id", default="run-offline-0001")
    parser.add_argument("--now", default=None, help="ISO 时间，用于可复现运行")
    parser.add_argument("--policy", default=None, help="从真实政策文件编译配置，而不是读夹具里的 policy-config.json")
    parser.add_argument(
        "--proceed-on-policy-conflict",
        action="store_true",
        help="政策自述版本冲突时仍继续（会在清单里记 PARTIAL）",
    )
    args = parser.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else None
    outcome = run_case(
        args.case,
        args.out,
        args.run_id,
        now,
        policy_doc=args.policy,
        proceed_on_policy_conflict=args.proceed_on_policy_conflict,
    )
    manifest = outcome["manifest"]

    print(f"run_status={manifest.get('run_status')} delivery_status={manifest.get('delivery_status')}")
    if outcome["failed_stage"]:
        print(f"FAILED at {outcome['failed_stage']}: {outcome['error']}")
        return 1

    print(f"candidates={manifest['counts']['candidates']} selected={manifest['counts']['selected']}")
    for item in outcome["delivery"]["items"]:
        print(f"  #{item['priority_rank']} {item['candidate_id']} [{item['grouping']}] {item['recommended_action']}")
    if outcome["outcome"].lock and outcome["outcome"].lock.reclaimed:
        print("警告：接管了一个失效的锁（上次运行可能异常退出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
