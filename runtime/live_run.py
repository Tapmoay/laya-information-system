"""在线单轮：抓取 → 规范化 → 测量 → 选择 → 交付 → 推送（M2 链路）。

与离线入口的区别只有前半段：候选来自真实 HTTP，而不是夹具。
选择、交付、清单与离线入口完全一致——这正是"来源与模型可替换"的验证点。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capabilities.adapters.models.invoke import build_request, invoke_decision
from capabilities.adapters.models.measurement import to_measurement
from capabilities.adapters.models.registry import get_profile, load_profiles
from capabilities.adapters.models.resolve import resolve_adapter
from delivery.builder import build_delivery
from delivery.push import PushConfig, push_brief
from delivery.renderer import render_brief
from ingest.dedupe import dedupe_candidates
from ingest.fetch import FetchPolicy, fetch
from ingest.normalize import build_candidate
from ingest.urls import host_of
from runtime.manifest_shared import finalize_manifest
from runtime.run_manager import RunManager, StageSpec
from selection.engine import load_policy, select
from tools.validate_objects import validate

CONTRACT_VERSIONS = {
    "POLICY-CONFIG": "1.0",
    "DEMAND-SNAPSHOT": "1.0",
    "FETCH-RESULT": "1.0",
    "CANDIDATE-RECORD": "1.0",
    "MEASUREMENT-RECORD": "1.0",
    "SELECTION-DECISION": "1.0",
    "DELIVERY-OBJECT": "1.0",
    "RENDERED-BRIEF": "1.0",
    "DELIVERY-STATUS": "1.0",
    "RUN-MANIFEST": "1.0",
}

RELEVANCE_OPTIONS = ["DIRECT", "ADJACENT", "EXPLORATORY", "UNRELATED", "INSUFFICIENT_EVIDENCE"]


@dataclass
class LiveRunConfig:
    urls: list[str]
    demand: dict[str, Any]
    policy_config: dict[str, Any]
    fetch_policy: FetchPolicy
    push: PushConfig = field(default_factory=PushConfig)
    model_profile_id: str = "MODEL-PROFILE-STUB-AGENT"
    judgment_language: str = "en"
    source_language: str = "en"
    discovery_method: str = "MANUAL"
    allow_unvalidated: bool = True
    policy_path: str | None = None
    policy_doc: str | None = None
    proceed_on_policy_conflict: bool = False


def _require_valid(kind: str, payload: dict[str, Any], label: str) -> None:
    problems = validate(kind, payload)
    if problems:
        raise ValueError(f"{label} 违反 {kind} 契约：" + "; ".join(problems))


def _partial_if_any(items: list[Any]) -> str:
    return "PARTIAL" if items else "OK"


def run_live(
    config: LiveRunConfig,
    *,
    run_id: str,
    out_dir: Path | str | None = None,
    now: datetime | None = None,
    trigger: str = "MANUAL",
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    out_path = Path(out_dir) if out_dir is not None else None
    if out_path is not None:
        out_path.mkdir(parents=True, exist_ok=True)

    policy = load_policy(config.policy_path)
    profile = get_profile(load_profiles(), config.model_profile_id)
    adapter = resolve_adapter(profile)

    policy_config = config.policy_config

    def precheck(context) -> dict[str, Any]:
        nonlocal policy_config
        if config.policy_doc:
            from policy.compiler import compile_policy_config
            from policy.loader import load_policy_bundle

            bundle = load_policy_bundle(
                config.policy_doc,
                proceed_allowed=config.proceed_on_policy_conflict,
                bundle_id=f"bundle-{run_id}",
                compiled_at=now,
            )
            if bundle["state"]["status"] == "INVALID":
                raise ValueError(
                    "政策包状态为 INVALID（"
                    + str(bundle["state"].get("reason_code"))
                    + "）：自述版本冲突且未获准继续"
                )
            policy_config = compile_policy_config(
                bundle, config_id=f"config-{run_id}", compiled_at=now
            )

        _require_valid("demand-snapshot", config.demand, "demand-snapshot")
        _require_valid("policy-config", policy_config, "policy-config")
        if not config.allow_unvalidated and (
            profile["implementation_kind"] != "REAL" or profile["status"] != "ACTIVE"
        ):
            raise ValueError(
                f"{profile['model_profile_id']} 是未验证实现（{profile['implementation_kind']}/{profile['status']}），"
                "必须在配置里显式写 allow_unvalidated: true 才能使用"
            )
        if not config.urls:
            raise ValueError("没有配置任何来源 URL")
        for url in config.urls:
            host = host_of(url)
            if not any(host == item or host.endswith("." + item) for item in config.fetch_policy.allowed_hosts):
                raise ValueError(f"来源不在白名单内：{url}")
        return {"profile": profile, "urls": list(config.urls), "policy_config": policy_config}

    def discovery(context) -> dict[str, Any]:
        results = [
            fetch(url, request_id=f"{run_id}-fetch-{index:02d}", policy=config.fetch_policy, now=now)
            for index, url in enumerate(config.urls, start=1)
        ]
        return {"fetch_results": results}

    def normalization(context) -> dict[str, Any]:
        fetches = context.get("DISCOVERY")["fetch_results"]
        candidates: list[dict[str, Any]] = []
        texts: dict[str, str] = {}
        failures: list[str] = []
        for index, result in enumerate(fetches, start=1):
            if result["outcome"] == "ERROR":
                failures.append(result["request_id"])
                continue
            candidate_id = f"cand-{run_id}-{index:02d}"
            bundle = build_candidate(
                result,
                candidate_id=candidate_id,
                observed_at=now,
                discovery_method=config.discovery_method,
            )
            candidates.append(bundle["candidate"])
            texts[candidate_id] = result.get("text") or bundle["candidate"]["title"]["text"]
        unique, duplicates = dedupe_candidates(candidates)
        return {"candidates": unique, "duplicates": duplicates, "texts": texts, "failures": failures}

    def measurement(context) -> dict[str, Any]:
        from judgment.router import ESCALATE, apply as apply_routing

        profile_payload = context.get("PRECHECK")["profile"]
        norm = context.get("NORMALIZATION")
        measurements: list[dict[str, Any]] = []
        escalations: list[dict[str, Any]] = []
        for candidate in norm["candidates"]:
            text = norm["texts"].get(candidate["candidate_id"], candidate["title"]["text"])
            source_language = candidate["source_language"]["tag"]
            if source_language in {"MIXED", "UNKNOWN"}:
                source_language = config.source_language
            request = build_request(
                request_id=f"req-{candidate['candidate_id']}",
                capability_id="CAP-MEA-02",
                candidate_id=candidate["candidate_id"],
                candidate_text=text[:20000],
                options=RELEVANCE_OPTIONS,
                instruction="按给定标准判定候选与当前目标的关系",
                created_at=now,
                source_language=source_language,
                judgment_language=config.judgment_language,
                evidence_refs=[candidate["content"]["text_ref"]],
            )
            result = invoke_decision(
                adapter, request, profile_payload, allow_unvalidated=config.allow_unvalidated
            )
            routing = None
            if result.get("probabilities"):
                # 模型给了概率：先由代码判断"有没有分歧"
                result, routing = apply_routing(result)
                if routing.route == ESCALATE:
                    escalations.append(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "request_id": request["request_id"],
                            "capability_id": request["capability_id"],
                            "text_ref": candidate["content"]["text_ref"],
                            "routing": routing.as_dict(),
                        }
                    )
            measurements.append(
                to_measurement(
                    result, request, profile_payload, now, unvalidated=config.allow_unvalidated
                )
            )
        return {"measurements": measurements, "escalations": escalations}

    def selection(context) -> dict[str, Any]:
        result = select(
            candidates=context.get("NORMALIZATION")["candidates"],
            measurements=context.get("MEASUREMENT")["measurements"],
            demand=config.demand,
            policy_config=context.get("PRECHECK")["policy_config"],
            policy=policy,
            decided_at=now,
        )
        for decision in result.decisions:
            _require_valid("selection-decision", decision, decision["selection_id"])
        return {
            "decisions": result.decisions,
            "delivery_order": result.delivery_order,
            "actions": result.actions,
            "constraint_checks": result.constraint_checks,
        }

    def delivery(context) -> dict[str, Any]:
        norm = context.get("NORMALIZATION")
        sel = context.get("SELECTION")
        payload = build_delivery(
            delivery_id=f"del-{run_id}",
            built_at=now,
            candidates_by_id={item["candidate_id"]: item for item in norm["candidates"]},
            decisions=sel["decisions"],
            delivery_order=sel["delivery_order"],
            actions=sel["actions"],
            policy_config=config.policy_config,
        )
        _require_valid("delivery-object", payload, payload["delivery_id"])
        brief = render_brief(
            payload,
            now,
            decisions=sel["decisions"],
            measurements=context.get("MEASUREMENT")["measurements"],
        )
        _require_valid("rendered-brief", brief, brief["brief_id"])
        return {"delivery": payload, "brief": brief}

    def push(context) -> dict[str, Any]:
        brief = context.get("DELIVERY_BUILD")["brief"]
        status = push_brief(brief, config.push, run_id, now)
        _require_valid("delivery-status", status, status["delivery_status_id"])
        return {"status": status}

    stages = [
        StageSpec("PRECHECK", precheck),
        StageSpec(
            "DISCOVERY",
            discovery,
            status_from_payload=lambda payload: _partial_if_any(
                [item for item in payload["fetch_results"] if item["outcome"] == "ERROR"]
            ),
        ),
        StageSpec(
            "NORMALIZATION",
            normalization,
            status_from_payload=lambda payload: "OK" if payload["candidates"] else "PARTIAL",
        ),
        StageSpec(
            "MEASUREMENT",
            measurement,
            status_from_payload=lambda payload: _partial_if_any(
                [item for item in payload["measurements"] if item["state"]["status"] != "OK"]
            ),
        ),
        StageSpec("SELECTION", selection, cacheable=True),
        StageSpec("DELIVERY_BUILD", delivery, cacheable=True),
        StageSpec(
            "PUSH",
            push,
            skip=config.push.channel == "none",
            partial_on_error=True,
            status_from_payload=lambda payload: "PARTIAL"
            if payload.get("status", {}).get("status") == "FAILED"
            else "OK",
        ),
    ]

    artifacts: list[str] = []
    manifest: dict[str, Any] = {}

    def on_complete(outcome) -> None:
        push_payload = outcome.payloads.get("PUSH") or {}
        push_stage = next((stage for stage in outcome.stages if stage.name == "PUSH"), None)
        if push_stage is None or push_stage.status == "SKIPPED":
            delivery_status = "NOT_ATTEMPTED"
        elif push_stage.status == "PARTIAL":
            delivery_status = "FAILED"
        else:
            raw = push_payload.get("status", {}).get("status", "NOT_ATTEMPTED")
            delivery_status = "SUCCESS" if raw in {"SUCCESS", "ALREADY_SENT"} else raw

        norm = outcome.payloads.get("NORMALIZATION") or {}
        meas = outcome.payloads.get("MEASUREMENT") or {}
        sel = outcome.payloads.get("SELECTION") or {}
        built = outcome.payloads.get("DELIVERY_BUILD") or {}

        written: dict[str, Any] = {}
        if outcome.payloads.get("DISCOVERY"):
            written["fetch-results.json"] = outcome.payloads["DISCOVERY"]["fetch_results"]
        if norm:
            written["candidates.json"] = norm["candidates"]
            written["duplicates.json"] = norm["duplicates"]
        if meas:
            written["measurements.json"] = meas["measurements"]
        if sel:
            written["selection-decisions.json"] = sel["decisions"]
        if built:
            written["delivery-object.json"] = built["delivery"]
            written["rendered-brief.json"] = built["brief"]
        if push_payload.get("status"):
            written["delivery-status.json"] = push_payload["status"]

        if config.policy_doc:
            from policy.loader import load_policy_bundle

            written["policy-bundle.json"] = load_policy_bundle(
                config.policy_doc,
                proceed_allowed=config.proceed_on_policy_conflict,
                bundle_id=f"bundle-{run_id}",
                compiled_at=now,
            )
            written["policy-config.json"] = policy_config

        problems = [
            stage for stage in outcome.stages
            if stage.status in {"FAILED", "PARTIAL"} and stage.error
        ]
        if problems:
            written["errors.json"] = {stage.name: stage.error for stage in problems}

        if out_path is not None:
            for name, payload in written.items():
                (out_path / name).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                artifacts.append(name)

        manifest.update(
            finalize_manifest(
                run_id=run_id,
                trigger=trigger,
                at=now,
                timezone="Asia/Shanghai",
                policy_version=(outcome.payloads.get("PRECHECK") or {}).get("policy_config", policy_config).get("policy_version", "UNKNOWN"),
                contract_versions=CONTRACT_VERSIONS,
                model_revisions=[
                    {"model_profile_id": profile["model_profile_id"], "model_revision": profile["revision"]}
                ],
                stages=[stage.as_manifest() for stage in outcome.stages],
                delivery_status=delivery_status,
                counts={
                    "candidates": len(norm.get("candidates", [])),
                    "measurements": len(meas.get("measurements", [])),
                    "selected": len(sel.get("delivery_order", [])),
                    "delivered": len(built.get("delivery", {}).get("items", []))
                    if delivery_status == "SUCCESS"
                    else 0,
                },
                artifacts=artifacts + (["run-manifest.json"] if out_path is not None else []),
            )
        )
        if out_path is not None:
            (out_path / "run-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        _require_valid("run-manifest", manifest, run_id)

    lock_path = (
        out_path / ".run.lock"
        if out_path is not None
        else Path(tempfile.mkdtemp(prefix="laya-live-")) / "run.lock"
    )
    _budget = (config.policy_config.get("run_limits") or {}).get("max_duration_minutes")
    manager = RunManager(
        lock_path=lock_path,
        run_id=run_id,
        clock=lambda: now,
        max_duration_seconds=(float(_budget) * 60 if _budget else None),
    )
    outcome = manager.execute(stages, {"urls": config.urls}, on_complete)

    return {
        "run_id": run_id,
        "manifest": manifest,
        "failed_stage": outcome.failed_stage,
        "error": outcome.error,
        "candidates": (outcome.payloads.get("NORMALIZATION") or {}).get("candidates", []),
        "measurements": (outcome.payloads.get("MEASUREMENT") or {}).get("measurements", []),
        "decisions": (outcome.payloads.get("SELECTION") or {}).get("decisions", []),
        "delivery": (outcome.payloads.get("DELIVERY_BUILD") or {}).get("delivery"),
        "brief": (outcome.payloads.get("DELIVERY_BUILD") or {}).get("brief"),
        "push_status": (outcome.payloads.get("PUSH") or {}).get("status"),
    }
