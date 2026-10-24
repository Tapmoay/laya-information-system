"""把一批内容还原成事实，供去重与交付使用。

    python tools/extract_facts.py --items runs/crawl-<日期> --out runs/crawl-<日期>/facts.jsonl

模型的活只有一件：读出这条内容明确说了什么。比较、聚簇、判重全在确定性代码里
（judgment/facts.py），所以同一份输出永远得到同一组分簇。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.facts import build_prompt, parse  # noqa: E402
from judgment.scoring import summarise  # noqa: E402
from store.fact_library import append as append_library  # noqa: E402
from store.fact_library import entry_for, load as load_library, lookup  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="抽取内容陈述的事实")
    parser.add_argument("--items", required=True)
    parser.add_argument("--out", default=None, help="输出 facts.jsonl，默认写到 items 同目录")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--provider", choices=["agent", "local"], default="agent",
                        help="agent：只产出题面，由 AI/API 作答；local：用本机小模型（实测效果差）")
    parser.add_argument("--import-answers", default=None, help="把 agent 填好的答案解析成 facts.jsonl")
    # 本地 3B 权重已于 2026-10-22 退役（ADR-0031）；`--provider local` 需要自己重新下载并指路径。
    parser.add_argument("--model", default="models/Qwen2.5-3B-Instruct", help="仅 --provider local 使用")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--chars", type=int, default=1200)
    parser.add_argument("--scores", default=None, help="打分行；给了就只还原达到粗筛线的条目")
    parser.add_argument("--min-score", type=float, default=0.40)
    parser.add_argument("--library", default=None, help="事实库；给了就先查库、再把新事实写回去")
    args = parser.parse_args(argv)

    path = Path(args.items)
    if path.is_dir():
        path = path / "items.json"
    out = Path(args.out) if args.out else path.parent / "facts.jsonl"
    items = json.loads(path.read_text(encoding="utf-8-sig"))

    # 第一段：便宜地判掉重复。事实还原要跑模型，不能对全量做——
    # 绝大多数内容在这一步就该被排除，为它们付 GPU 时间是浪费。
    library = load_library(args.library) if args.library else []
    skipped = 0
    kept = []
    for item in items:
        verdict = lookup(item, library) if library else {"verdict": "NEW"}
        if verdict["verdict"] == "IDENTICAL":
            skipped += 1
            print("  跳过（库里已有同样的说法）", (item.get("title") or "")[:40])
            continue
        kept.append(item)
    if library:
        print("查库：跳过 {} 条，进入还原 {} 条".format(skipped, len(kept)))

    if args.scores:
        table = {}
        for line in Path(args.scores).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row.get("scores"), dict):
                table[row["item_id"]] = float(summarise(row["scores"])["score"])
        kept = [item for item in kept if table.get(item["item_id"], 0.0) >= args.min_score]
        print("粗筛后进入还原 {} 条（线 {}）".format(len(kept), args.min_score))

    items = kept[: args.limit]
    if not items:
        print("没有需要还原的条目")
        return 0

    # 事实还原是生成任务，交给 AI/API 做。
    # 本机的 2B 判断模型是"是/否"判定器，不该拿它生成结构化事实——
    # 实测 3B 指令模型都抽不出可用三元组（见 docs/30 第 6 节）。
    if args.import_answers:
        answers = {}
        for line in Path(args.import_answers).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            answers[row["item_id"]] = row.get("raw") or ""
        rows = []
        for item in items:
            claims = parse(answers.get(item["item_id"], ""))
            rows.append({
                "item_id": item["item_id"],
                "title": item.get("title"),
                "claims": [
                    {"subject": c.subject, "relation": c.relation, "object": c.object, "time": c.time, "key": c.key}
                    for c in claims
                ],
                "raw": answers.get(item["item_id"], ""),
            })
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + chr(10), encoding="utf-8")
        print("解析完成", out, "共", len(rows), "条，空事实", sum(1 for r in rows if not r["claims"]))
        if args.library:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            by_id = {r["item_id"]: r for r in rows}
            entries = [entry_for(i, by_id[i["item_id"]]["claims"], now=now) for i in items if by_id.get(i["item_id"], {}).get("claims")]
            print("写进事实库", append_library(args.library, entries), "条 →", args.library)
        return 0

    if args.provider == "agent":
        packet = out.with_suffix(".packet.json")
        rows = []
        for item in items:
            system, user = build_prompt(item.get("title") or "", item.get("text_clean") or item.get("text") or "", chars=args.chars)
            rows.append({
                "item_id": item["item_id"],
                "title": item.get("title"),
                "system": system,
                "user": user,
                "answer_format": "主体|关系|对象|时间，每条一行，最多六行；没有可陈述的事实时写 NONE",
            })
        packet.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
        answers = packet.with_name("facts.answers.jsonl")
        print("已写出题面", packet, "共", len(rows), "条")
        print("把答案按 {item_id, raw} 一行一条写进", answers)
        print("然后运行：python tools/extract_facts.py --items", args.items, "--import-answers", answers, "--out", out)
        return 0

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.bfloat16, device_map=args.device, trust_remote_code=True
    )
    model.eval()

    def generate(system: str, user: str) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            output = model.generate(
                **inputs, max_new_tokens=256, do_sample=False, pad_token_id=tokenizer.eos_token_id
            )
        generated = output[0][inputs["input_ids"].shape[1] :]
        return tokenizer.decode(generated, skip_special_tokens=True).strip()

    rows = []
    for index, item in enumerate(items, 1):
        system, user = build_prompt(item.get("title") or "", item.get("text_clean") or item.get("text") or "", chars=args.chars)
        raw = generate(system, user)
        claims = parse(raw)
        rows.append(
            {
                "item_id": item["item_id"],
                "title": item.get("title"),
                "claims": [
                    {"subject": c.subject, "relation": c.relation, "object": c.object, "time": c.time, "key": c.key}
                    for c in claims
                ],
                "raw": raw,
            }
        )
        print("  [{}/{}] {} 条事实  {}".format(index, len(items), len(claims), (item.get("title") or "")[:44]))

    out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    empty = sum(1 for row in rows if not row["claims"])
    print("写入", out, "共", len(rows), "条，其中", empty, "条没抽到事实")
    if args.library:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        by_id = {row["item_id"]: row for row in rows}
        entries = [
            entry_for(item, by_id[item["item_id"]]["claims"], now=now)
            for item in items
            if by_id.get(item["item_id"], {}).get("claims")
        ]
        print("写进事实库", append_library(args.library, entries), "条 →", args.library)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
