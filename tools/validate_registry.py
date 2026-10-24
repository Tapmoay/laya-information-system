"""能力注册表校验器（WP-R1 的可执行部分）。

职责：
  1. 用 JSON Schema 校验 capabilities/registry.yaml 的结构。
  2. 逐项运行注册表内声明的 INV-* 不变量，确认"文档里写了"等于"代码里查了"。
  3. 报告尚未落地的对象契约（schema_ref），作为 WP-R2 的自动生成待办。
  4. 生成人类可读索引表，避免 docs/ 与 YAML 两份事实源漂移。

用法：
    python tools/validate_registry.py            # 校验，失败退出码 1
    python tools/validate_registry.py --index     # 打印 markdown 索引
    python tools/validate_registry.py --backlog   # 打印未落地契约清单
    python tools/validate_registry.py --json      # 机器可读结果
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "capabilities" / "registry.yaml"
CAP_SCHEMA_PATH = ROOT / "contracts" / "capability-contract.schema.json"
REG_SCHEMA_PATH = ROOT / "contracts" / "capability-registry.schema.json"
SCHEMA_DIR = ROOT / "contracts" / "schemas"

CAP_ID_RE = re.compile(r"^CAP-(POL|DEM|INF|MEA|SEL|DEL|RUN|FBK|PUP)-[0-9]{2}$")

# 能力标识与名称中不应出现的具体模型/厂商名。新增候选模型时按需扩充。
VENDOR_TOKENS = (
    "laya", "strands", "qwen", "gemma", "jev", "kev",
    "llama", "mistral", "openai", "anthropic", "claude", "gpt",
)

BUSINESS_ORDER = {
    "POLICY": 0,
    "DEMAND": 1,
    "INFORMATION": 2,
    "MEASUREMENT": 3,
    "SELECTION": 4,
    "DELIVERY": 5,
    "FEEDBACK": 6,
    "POLICY_UPDATE": 7,
    # RUNTIME 横切各阶段，不参与单向顺序约束
}

MILESTONE_RANK = {"M1": 1, "M2": 2, "M3": 3}

SELECTION_OWNERS = {"RULE_SET", "DETERMINISTIC_CODE"}
MODEL_OWNERS = {"MODEL_ADAPTER", "HYBRID", "AI_AGENT"}
EXTERNAL_EFFECT_STAGES = {"DELIVERY", "POLICY_UPDATE"}


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是映射")
    return data


def validate_schema(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reg_schema = json.loads(REG_SCHEMA_PATH.read_text(encoding="utf-8"))
    cap_schema = json.loads(CAP_SCHEMA_PATH.read_text(encoding="utf-8"))

    for err in jsonschema.Draft202012Validator(reg_schema).iter_errors(data):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"[registry] {loc}: {err.message}")

    validator = jsonschema.Draft202012Validator(cap_schema)
    for cap in data.get("capabilities", []):
        cid = cap.get("capability_id", "<unknown>")
        for err in validator.iter_errors(cap):
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            errors.append(f"[{cid}] {loc}: {err.message}")
    return errors


def caps(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data.get("capabilities", []))


# --------------------------------------------------------------------------
# 不变量实现：每个返回违规描述列表，空列表表示通过
# --------------------------------------------------------------------------

def _inv_01_unique_ids(data: dict[str, Any]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for cap in caps(data):
        cid = cap["capability_id"]
        seen[cid] = seen.get(cid, 0) + 1
        if not CAP_ID_RE.match(cid):
            out.append(f"{cid}: capability_id 格式不匹配 CAP-<阶段码>-NN")
    for cid, n in seen.items():
        if n > 1:
            out.append(f"{cid}: 能力 ID 重复出现 {n} 次")
    return out


def _inv_02_selection_owner(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for cap in caps(data):
        if cap.get("stage") != "SELECTION":
            continue
        if cap.get("owner_type") not in SELECTION_OWNERS:
            out.append(
                f"{cap['capability_id']}: SELECTION 阶段的责任方为 {cap.get('owner_type')}，"
                "只允许 RULE_SET 或 DETERMINISTIC_CODE（RULE_ENGINE_NO_MODEL）"
            )
        if cap.get("model_required"):
            out.append(f"{cap['capability_id']}: SELECTION 阶段不得声明 model_required=true")
    return out


def _inv_03_model_required_owner(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for cap in caps(data):
        if cap.get("model_required") and cap.get("owner_type") not in MODEL_OWNERS:
            out.append(
                f"{cap['capability_id']}: model_required=true 但 owner_type={cap.get('owner_type')}，"
                "不允许（模型的归属必须显式）"
            )
    return out


def _inv_04_adapter_requires_model(data: dict[str, Any]) -> list[str]:
    return [
        f"{cap['capability_id']}: MODEL_ADAPTER 能力必须声明 model_required=true"
        for cap in caps(data)
        if cap.get("owner_type") == "MODEL_ADAPTER" and not cap.get("model_required")
    ]


def _inv_05_dependency_integrity(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    known = {cap["capability_id"] for cap in caps(data)}
    graph: dict[str, list[str]] = {}
    for cap in caps(data):
        cid = cap["capability_id"]
        deps = list(cap.get("depends_on") or [])
        graph[cid] = deps
        for dep in deps:
            if dep == cid:
                out.append(f"{cid}: 依赖自身")
            elif dep not in known:
                out.append(f"{cid}: 依赖不存在的 {dep}")

    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {cid: WHITE for cid in graph}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GREY
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if color[nxt] == GREY:
                cycle = " -> ".join(stack + [nxt])
                out.append(f"依赖成环: {cycle}")
            elif color[nxt] == WHITE:
                visit(nxt, stack + [nxt])
        color[node] = BLACK

    for cid in graph:
        if color[cid] == WHITE:
            visit(cid, [cid])
    return out


def _inv_06_stage_coverage(data: dict[str, Any]) -> list[str]:
    declared = [s["id"] for s in data.get("stages", [])]
    used = {cap["stage"] for cap in caps(data)}
    out = [f"stages 重复声明: {sid}" for sid in set(declared) if declared.count(sid) > 1]
    out += [f"阶段 {sid} 没有任何能力" for sid in declared if sid not in used]
    out += [f"能力使用了未声明的阶段: {sid}" for sid in used if sid not in declared]
    return out


def _inv_07_traceability(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for cap in caps(data):
        fields = list(cap.get("trace_fields") or [])
        if not fields:
            out.append(f"{cap['capability_id']}: 缺少 trace_fields")
        dupes = {f for f in fields if fields.count(f) > 1}
        for f in sorted(dupes):
            out.append(f"{cap['capability_id']}: trace_field {f} 重复")
    return out


def _inv_08_vendor_neutral(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for cap in caps(data):
        haystack = f"{cap['capability_id']} {cap['name']}".lower()
        for token in VENDOR_TOKENS:
            if re.search(rf"(^|[^a-z]){re.escape(token)}([^a-z]|$)", haystack):
                out.append(f"{cap['capability_id']}: 标识中出现厂商/模型名 '{token}'")
    return out


def _inv_09_external_effects_authorized(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for cap in caps(data):
        if not cap.get("external_effects"):
            continue
        basis = (cap.get("authorization_basis") or "").strip()
        if len(basis) < 6:
            out.append(f"{cap['capability_id']}: 有外部副作用但缺少 authorization_basis")
    return out


def _inv_10_external_stage_bound(data: dict[str, Any]) -> list[str]:
    return [
        f"{cap['capability_id']}: {cap['stage']} 阶段不得有外部副作用"
        for cap in caps(data)
        if cap.get("external_effects") and cap.get("stage") not in EXTERNAL_EFFECT_STAGES
    ]


def _inv_11_stage_direction(data: dict[str, Any]) -> list[str]:
    order = {cap["capability_id"]: BUSINESS_ORDER.get(cap["stage"]) for cap in caps(data)}
    out: list[str] = []
    for cap in caps(data):
        src_rank = BUSINESS_ORDER.get(cap["stage"])
        if src_rank is None:
            continue
        for dep in cap.get("depends_on") or []:
            dep_rank = order.get(dep)
            if dep_rank is None or dep_rank <= src_rank:
                continue
            out.append(
                f"{cap['capability_id']}({cap['stage']}) 依赖了业务循环中更靠后的 "
                f"{dep}，阶段顺序被倒置"
            )
    return out


INVARIANTS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "INV-01": _inv_01_unique_ids,
    "INV-02": _inv_02_selection_owner,
    "INV-03": _inv_03_model_required_owner,
    "INV-04": _inv_04_adapter_requires_model,
    "INV-05": _inv_05_dependency_integrity,
    "INV-06": _inv_06_stage_coverage,
    "INV-07": _inv_07_traceability,
    "INV-08": _inv_08_vendor_neutral,
    "INV-09": _inv_09_external_effects_authorized,
    "INV-10": _inv_10_external_stage_bound,
    "INV-11": _inv_11_stage_direction,
}


def check_invariants(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    declared = {inv["id"] for inv in data.get("invariants", [])}

    for missing in sorted(set(INVARIANTS) - declared):
        errors.append(f"{missing}: 代码已实现但注册表未声明")
    for unknown in sorted(declared - set(INVARIANTS)):
        errors.append(f"{unknown}: 注册表声明了不变量，但校验器没有实现")

    for inv_id, fn in INVARIANTS.items():
        if inv_id not in declared:
            continue
        for msg in fn(data):
            errors.append(f"{inv_id}: {msg}")
    return errors


def uncovered_schema_refs(data: dict[str, Any]) -> list[str]:
    refs: set[str] = set()
    for cap in caps(data):
        for io in list(cap.get("inputs") or []) + list(cap.get("outputs") or []):
            refs.add(io["schema_ref"])
    missing = []
    for ref in sorted(refs):
        name, _, version = ref.partition("/")
        candidate = SCHEMA_DIR / f"{name.lower().replace('_', '-')}-{version}.schema.json"
        if not candidate.exists():
            missing.append(ref)
    return missing


def fixture_gaps(data: dict[str, Any]) -> list[dict[str, str]]:
    """列出"较早里程碑能力依赖较晚里程碑能力"的边。

    这些边不是错误：早期里程碑用夹具替代尚未实现的产出方。
    列出它们是为了让"这一批到底需要哪些夹具"可被自动推导，而不是靠人记。
    """
    by_id = {cap["capability_id"]: cap for cap in caps(data)}
    out: list[dict[str, str]] = []
    for cap in caps(data):
        cap_rank = MILESTONE_RANK[cap["milestone"]]
        for dep in cap.get("depends_on") or []:
            dep_cap = by_id.get(dep)
            if dep_cap is None:
                continue
            if MILESTONE_RANK[dep_cap["milestone"]] > cap_rank:
                out.append({
                    "capability": cap["capability_id"],
                    "milestone": cap["milestone"],
                    "depends_on": dep,
                    "dep_milestone": dep_cap["milestone"],
                    "fixture_ref": next(
                        (io["schema_ref"] for io in cap["inputs"] if io.get("source_capability") == dep),
                        dep_cap["outputs"][0]["schema_ref"],
                    ),
                })
    return out


def render_index(data: dict[str, Any]) -> str:
    lines = [
        "| 能力 ID | 名称 | 阶段 | 责任方 | 状态 | 里程碑 |",
        "|---|---|---|---|---|---|",
    ]
    for cap in caps(data):
        lines.append(
            f"| {cap['capability_id']} | {cap['name']} | {cap['stage']} | "
            f"{cap['owner_type']} | {cap['status']} | {cap['milestone']} |"
        )
    return "\n".join(lines)


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    from collections import Counter

    return {
        "capabilities": len(caps(data)),
        "by_stage": dict(Counter(c["stage"] for c in caps(data))),
        "by_owner": dict(Counter(c["owner_type"] for c in caps(data))),
        "by_status": dict(Counter(c["status"] for c in caps(data))),
        "by_milestone": dict(Counter(c["milestone"] for c in caps(data))),
        "missing_contracts": len(uncovered_schema_refs(data)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验能力注册表")
    parser.add_argument("--index", action="store_true", help="输出 markdown 索引表")
    parser.add_argument("--backlog", action="store_true", help="输出尚未落地的对象契约")
    parser.add_argument("--fixtures", action="store_true", help="输出跨里程碑依赖（需要在早期用夹具替代）")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON 结果")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = load_registry()
    errors = validate_schema(data) + check_invariants(data)

    if args.as_json:
        print(json.dumps(
            {"ok": not errors, "errors": errors, "summary": summarize(data)},
            ensure_ascii=False, indent=2,
        ))
        return 0 if not errors else 1

    if args.index:
        print(render_index(data))
        return 0

    if args.backlog:
        for ref in uncovered_schema_refs(data):
            print(ref)
        return 0

    if args.fixtures:
        gaps = fixture_gaps(data)
        if not gaps:
            print("没有跨里程碑依赖。")
            return 0
        for gap in gaps:
            print(
                f"{gap['capability']} ({gap['milestone']}) 依赖 "
                f"{gap['depends_on']} ({gap['dep_milestone']})"
                f"  -> 早期需夹具提供 {gap['fixture_ref']}"
            )
        return 0

    summary = summarize(data)
    print(f"能力数: {summary['capabilities']}  阶段分布: {summary['by_stage']}")
    print(f"责任方: {summary['by_owner']}  里程碑: {summary['by_milestone']}")
    print(f"未落地契约: {summary['missing_contracts']}")

    if errors:
        print(f"\n发现 {len(errors)} 处违规：")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("\nOK: schema 与全部不变量通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
