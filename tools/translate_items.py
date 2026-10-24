"""把非中文条目的标题与摘要翻成中文，供标注页和最终产物阅读。

语言不是筛选条件，是展示条件：内容值不值得进来，由 13 问测量和它的分数决定，
与它用哪种语言写无关。所以翻译只发生在**最后一步**，而且只翻标题与摘要——
标注页和最终产物上人就只看这两样，翻全文既慢又没用。

两种后端：

- `--provider api`（默认）：DeepSeek `deepseek-flash`，**关思考模式**、`temperature=0`。
  失败不阻塞：该条保留原文标题，错误写进 `translate.log.jsonl`，事后可查。
- `--provider local`：本地 Qwen2.5-3B，留给离线场景（权重已退役，见 models/registry.yaml）。

每日链路只翻**最终选中的那几条**（`--selection`）：`final_digest` 只用这些译文，
翻全部非中文条目是纯浪费。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from judgment.text import summarise  # noqa: E402

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_API_MODEL = "deepseek-flash"
DEFAULT_LOCAL_MODEL = "models/Qwen2.5-3B-Instruct"

SYSTEM = (
    "你是专业译者。把给定文本译成简体中文，只输出译文本身，不要解释、不要加注。"
    "专有名词、模型名、公司名、论文名、代码标识符一律保留原文，不要翻译成中文。"
)
USER = "请把下面这段文字译成简体中文：\n\n{text}"


def _needs_zh(item: dict[str, Any]) -> bool:
    """要不要翻，只看它是不是中文；英文、日文、德文一视同仁。"""
    lang = (item.get("detected_language") or item.get("declared_language") or "").lower()
    if lang.startswith("zh"):
        return False
    if lang:
        return True
    sample = (item.get("text_clean") or item.get("text") or "")[:400]
    if not sample:
        return False
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return cjk < len(sample) * 0.3


def _selected_ids(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    selection = data.get("selection") or data
    ids: set[str] = set()
    for key in ("core", "exploration"):
        for row in selection.get(key) or []:
            if row.get("item_id"):
                ids.add(row["item_id"])
    return ids


def _api_key() -> str:
    """复用每日 runner 的取法：环境变量优先，其次 ~/.dsh/.credentials.yaml。"""
    from tools.run_daily import _api_key as daily_key

    return daily_key()


def _api_translate(text: str, *, key: str, model: str, timeout: float = 60.0) -> dict[str, Any]:
    """一次调用只翻一段。关思考、温度 0：翻译不需要推理，思考 token 也按输出计费。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER.format(text=text)},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 800,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = ""
    for attempt in (1, 2):
        request = urllib.request.Request(
            API_URL,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
            method="POST",
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
            if not content:
                raise RuntimeError("空译文")
            return {"text": content, "usage": data.get("usage") or {}, "ms": round((time.time() - started) * 1000)}
        except urllib.error.HTTPError as exc:
            last_error = "HTTP {}：{}".format(exc.code, exc.read().decode("utf-8", errors="replace")[:200])
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = "网络：{}".format(exc)
        except (RuntimeError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(1.5 * attempt)
    raise RuntimeError(last_error or "调用失败")


def _write_log(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + chr(10))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="把非中文条目的标题与摘要翻成中文")
    parser.add_argument("--items", required=True)
    parser.add_argument("--out", required=True, help="输出目录，写 translations.json")
    parser.add_argument("--selection", default=None, help="只翻这些被选中的条目（selection.json）")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--summary-chars", type=int, default=320)
    parser.add_argument("--provider", choices=["api", "local"], default="api")
    parser.add_argument("--model", default=None, help="默认 api=deepseek-flash，local=本地 3B")
    parser.add_argument("--workers", type=int, default=6, help="api 并发数")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--log", default=None, help="翻译日志，默认 <out>/translate.log.jsonl")
    args = parser.parse_args(argv)

    items_path = Path(args.items)
    if items_path.is_dir():
        items_path = items_path / "items.json"
    items = json.loads(items_path.read_text(encoding="utf-8-sig"))
    targets = [item for item in items if _needs_zh(item)]
    if args.selection:
        wanted = _selected_ids(Path(args.selection))
        targets = [item for item in targets if item["item_id"] in wanted]
    targets = targets[: args.limit]
    if not targets:
        print("没有需要翻译的条目")
        return 0
    print("需要翻译 {} 条（{}）".format(len(targets), args.provider))

    out = Path(args.out)
    translations: dict[str, dict[str, str]] = {}
    log_rows: list[dict[str, Any]] = []
    started = time.time()

    if args.provider == "api":
        key = _api_key()
        model = args.model or DEFAULT_API_MODEL
        jobs: list[tuple[str, str, str]] = []
        for item in targets:
            title = (item.get("title") or "").split(" | ")[0].strip()
            body = item.get("text_clean") or item.get("text") or ""
            summary = summarise(body, title, args.summary_chars)
            if title:
                jobs.append((item["item_id"], "title", title))
            if summary:
                jobs.append((item["item_id"], "summary", summary))

        def run(job: tuple[str, str, str]) -> dict[str, Any]:
            item_id, field, text = job
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                result = _api_translate(text, key=key, model=model)
                return {"item_id": item_id, "field": field, "ok": True, "at": stamp, **result}
            except Exception as exc:  # 单条失败不拖垮整批，日志里留证据
                return {"item_id": item_id, "field": field, "ok": False, "at": stamp,
                        "error": str(exc)[:300]}

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results = list(pool.map(run, jobs))
        for row in results:
            log_rows.append(row)
            if not row["ok"]:
                continue
            translations.setdefault(row["item_id"], {})[row["field"]] = row["text"]
        ok = sum(1 for row in results if row["ok"])
        tokens = sum(
            int((row.get("usage") or {}).get("prompt_tokens") or 0)
            + int((row.get("usage") or {}).get("completion_tokens") or 0)
            for row in results
        )
        print("调用 {} 次：成功 {}，失败 {}，{} tokens，{:.1f} 秒".format(
            len(results), ok, len(results) - ok, tokens, time.time() - started))
        for row in results:
            if not row["ok"]:
                print("  失败 {} {}：{}".format(row["item_id"], row["field"], row["error"]))
    else:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_path = args.model or DEFAULT_LOCAL_MODEL
        tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            model_path, torch_dtype=torch.bfloat16, device_map=args.device, trust_remote_code=True
        )
        model.eval()

        def translate(text: str) -> str:
            messages = [{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": USER.format(text=text)}]
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
            with torch.no_grad():
                output = model.generate(**inputs, max_new_tokens=512, do_sample=False,
                                        pad_token_id=tokenizer.eos_token_id)
            generated = output[0][inputs["input_ids"].shape[1]:]
            return tokenizer.decode(generated, skip_special_tokens=True).strip()

        for index, item in enumerate(targets, 1):
            title = (item.get("title") or "").split(" | ")[0].strip()
            body = item.get("text_clean") or item.get("text") or ""
            summary = summarise(body, title, args.summary_chars)
            translations[item["item_id"]] = {
                "title": translate(title) if title else "",
                "summary": translate(summary) if summary else "",
            }
            print("  [{}/{}] {}".format(index, len(targets), title[:40]))

    out.mkdir(parents=True, exist_ok=True)
    target = out / "translations.json"
    existing: dict[str, dict[str, str]] = {}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    existing.update(translations)
    target.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    print("写入", target, len(existing), "条")

    if args.provider == "api":
        log_path = Path(args.log) if args.log else out / "translate.log.jsonl"
        _write_log(log_path, log_rows)
        print("翻译日志：", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
