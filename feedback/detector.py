"""重复问题的检测（CAP-FBK-04）。

只做一件事：从运行清单与反馈记录里找出**反复出现的模式**，并给出证据。
它不建议怎么改——"改成什么"是提案与人的事（v0.3 §9.2 的观察与诊断两步，
不等于提出规则）。这样拆分是为了防止系统把"我注意到一个问题"直接变成"我改了规则"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

WINDOW = 5
PATIENCE = 3


@dataclass(frozen=True)
class Observation:
    code: str
    summary: str
    severity: str
    evidence_refs: list[str] = field(default_factory=list)
    window: int = WINDOW

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "summary": self.summary,
            "severity": self.severity,
            "evidence_refs": list(self.evidence_refs),
            "window": self.window,
            "contract_version": "1.0",
        }


def _discovery_partial(manifest: dict[str, Any]) -> bool:
    return any(
        stage["stage"] == "DISCOVERY" and stage["status"] in {"PARTIAL", "FAILED"}
        for stage in manifest.get("stages", [])
    )


def _evidence(manifests: Iterable[dict[str, Any]]) -> list[str]:
    """同一 run_id 只留一条：重跑会复用 run_id，重复引用会让观察对象违反契约。"""
    refs = [f"runs/{manifest['run_id']}/run-manifest.json" for manifest in manifests]
    return list(dict.fromkeys(refs))


def detect_observations(
    manifests: list[dict[str, Any]],
    feedback_records: list[dict[str, Any]] | None = None,
    *,
    window: int = WINDOW,
    patience: int = PATIENCE,
) -> list[Observation]:
    feedback_records = feedback_records or []
    if not manifests:
        return []
    recent = manifests[-window:]
    observations: list[Observation] = []

    unstable = [item for item in recent if _discovery_partial(item)]
    if len(unstable) >= patience:
        observations.append(
            Observation(
                code="SOURCE_UNSTABLE",
                summary=f"最近 {len(recent)} 轮里有 {len(unstable)} 轮抓取不完整，来源稳定性正在拖累链路",
                severity="WARN",
                evidence_refs=_evidence(unstable),
                window=len(recent),
            )
        )

    empty = [item for item in recent if item.get("counts", {}).get("selected", 0) == 0]
    if len(empty) >= patience:
        observations.append(
            Observation(
                code="EMPTY_DELIVERY_STREAK",
                summary=f"最近 {len(recent)} 轮里有 {len(empty)} 轮没有任何交付，可能是准入过严或来源枯竭",
                severity="WARN",
                evidence_refs=_evidence(empty),
                window=len(recent),
            )
        )

    delivered = sum(item.get("counts", {}).get("delivered", 0) for item in recent)
    stated = [item for item in feedback_records if item.get("kind") == "USER_STATED"]
    if delivered >= patience and not stated:
        observations.append(
            Observation(
                code="FEEDBACK_GAP",
                summary=f"最近 {len(recent)} 轮共交付 {delivered} 条，但没有收到任何用户明示反馈，收益无法判断",
                severity="INFO",
                evidence_refs=_evidence(recent),
                window=len(recent),
            )
        )

    return observations


def load_manifests(root: Path | str) -> list[dict[str, Any]]:
    root = Path(root)
    if root.is_file():
        return [json.loads(root.read_text(encoding="utf-8"))]
    manifests: list[dict[str, Any]] = []
    for path in sorted(root.rglob("run-manifest.json")):
        manifests.append(json.loads(path.read_text(encoding="utf-8")))
    manifests.sort(key=lambda item: item.get("started_at", ""))
    return manifests


def load_feedback(root: Path | str) -> list[dict[str, Any]]:
    path = Path(root) / "feedback.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="检测重复出现的问题")
    parser.add_argument("--runs", required=True, help="运行目录或单个 run-manifest.json")
    parser.add_argument("--feedback", default=None, help="反馈存储目录")
    args = parser.parse_args(argv)

    manifests = load_manifests(args.runs)
    feedback = load_feedback(args.feedback) if args.feedback else []
    observations = detect_observations(manifests, feedback)

    print(f"manifests={len(manifests)} feedback={len(feedback)} observations={len(observations)}")
    for item in observations:
        print(f"  [{item.severity}] {item.code}: {item.summary}")
        for ref in item.evidence_refs[:3]:
            print(f"      - {ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
