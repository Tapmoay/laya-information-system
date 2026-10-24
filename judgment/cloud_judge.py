"""云端判断：把固定的 13 个问句交给云端 API 测量，只产出测量，不做决策。

为什么可以上云（2026-10-24，用户决定，见 docs/05 的 ADR-0032）：
本地 Strands Decider 2B 受显存、速度、置信度三重限制；每日 200+ 条全量测量
在云端是每天一两块钱的量级，换来的是不占 GPU、并发可控、失败可留痕。

上云**不会**改变三条边界：

1. **测量不等于决策。** 这里只输出 13 个问句的 0–1 分与形态权重，
   不排序、不给总分、不选。分数怎么用仍由 `judgment/scoring.py` 的确定性规则决定。
2. **未知必须可表达。** 调用失败记成 failure，不写 0 分；正文为空或过短记成
   unscorable（由调用方写盘），不参与打分。把"没判断"伪装成"不值得"是禁止的。
3. **契约不随 provider 变。** 产出的 `scores` 结构与本地 decider 完全一致
   （每个问句 `{"noul": 0–1}`，形态 `{"probabilities": {...}}`），
   下游 scoring / selection 不需要知道这次是谁判断的。

一个必须说清的局限：**云端给的是自报分数，不是校准过的概率。**
本地 decider 的 noul 是 logit 头算出来的，云端是模型自己写的数字。
所以换模型之后，旧的阈值（0.45）不能直接沿用——它对应的是另一套分数分布。
换 provider 的流程因此固定为：小样本对照 → 分布对齐 → 再决定是否改阈值，
见 `agent/skills/personal-information-environment/references/cloud-judgment.md`。
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Iterable

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_WORKERS = 6
DEFAULT_RETRIES = 2
DEFAULT_TIMEOUT = 90.0
# 正文短于这个长度就不送测量：它不可判，不是 0 分。
MIN_MEASURE_CHARS = 40

FORM_LABELS = ("TECHNICAL", "PRODUCT", "EVENT", "RECRUIT", "MARKETING")
_MAX_NOTE = 300
_MAX_BAD_OUTPUT = 2000


class CloudJudgeError(RuntimeError):
    """一次测量失败。code 是稳定的机器可读原因，写进 judge.log.jsonl 与 failures.jsonl。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)[:400]
        super().__init__(f"{self.code}: {self.detail}" if self.detail else self.code)


@dataclass(frozen=True)
class JudgeOutput:
    scores: dict[str, Any]
    note: str
    usage: dict[str, int]
    attempts: int
    ms: int


# ---------------------------------------------------------------- 提示词


def noul_names(specs: Iterable[Any]) -> list[str]:
    return [spec.name for spec in specs if spec.kind == "noul"]


def _json_template(specs: list[Any]) -> str:
    scores = {name: 0.0 for name in noul_names(specs)}
    form = {label: 0.0 for label in FORM_LABELS}
    return json.dumps({"scores": scores, "form": form, "note": ""}, ensure_ascii=False)


def render_questions(specs: Iterable[Any]) -> str:
    """把问句渲染成编号清单。顺序固定，编号就是模型看到的题号。"""
    lines: list[str] = []
    for index, spec in enumerate(specs, 1):
        lines.append(f"{index}) {spec.name}：{spec.instructions}")
        if spec.criteria:
            for label in FORM_LABELS:
                if label in spec.criteria:
                    lines.append(f"   - {label}：{spec.criteria[label]}")
            lines.append("   给上面五个形态各一个 0–1 的权重，五者相加约等于 1。")
    return "\n".join(lines)


SYSTEM = (
    "你是一个信息测量员，不是推荐官。"
    "你的唯一任务是回答给定的测量问句：每个问句给一个 0 到 1 之间的分数。"
    "不要评价内容「值不值得推荐」，不要给总分，不要排序，不要替用户做取舍。"
    "只依据给出的标题与正文判断；原文没有证据就用低分，不要用外部知识补全事实。"
    "输出必须是单个 JSON 对象：不要 markdown 代码块，不要 JSON 之外的任何文字。"
)

SCALE_HELP = (
    "评分尺度（每个问句都用同一把尺子）：\n"
    "- 0.00：明确不符合，或正文里没有任何相关证据\n"
    "- 0.50：部分符合，或证据不足以下结论\n"
    "- 1.00：明确符合，正文里有直接证据\n"
    "请用两位小数，并让不同问句的分数有区分度；不要所有题都给 0.5。"
    "分数只表示该问句被满足的程度，不表示内容质量。"
)


def build_messages(
    specs: list[Any],
    title: str,
    text: str,
    *,
    repair: tuple[str, CloudJudgeError] | None = None,
) -> list[dict[str, str]]:
    """构造一次测量的消息。repair 非空时，把上一次的坏输出一起带回去修。"""
    body = text if len(text) <= 3500 else text[:3500]
    user = (
        "## 待测内容\n"
        f"标题：{title or '（无标题）'}\n"
        f"正文（可能被截断）：\n{body}\n\n"
        "## 测量问句\n"
        f"{render_questions(specs)}\n\n"
        f"{SCALE_HELP}\n\n"
        "## 输出格式（键名必须完全一致）\n"
        f"{_json_template(specs)}\n"
        "note 可选：一句话说明哪里证据不足（没有就留空字符串）。"
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
    if repair:
        bad, error = repair
        messages.append({"role": "assistant", "content": bad[:_MAX_BAD_OUTPUT]})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"上面的 JSON 不合法：{error.code} {error.detail}\n"
                    "请只输出修正后的 JSON 对象；键名、数值范围、形态权重之和都必须符合要求。"
                ),
            }
        )
    return messages


# ---------------------------------------------------------------- 解析


def _loads(content: str) -> dict[str, Any]:
    raw = (content or "").strip()
    if raw.startswith("```"):
        # 模型偶尔不听话，把 JSON 包进 ```json ... ```
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
        raw = raw.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CloudJudgeError("BAD_JSON", f"{exc.msg}（第 {exc.pos} 个字符）") from exc
    if not isinstance(payload, dict):
        raise CloudJudgeError("NOT_AN_OBJECT", type(payload).__name__)
    return payload


def _as_unit(value: Any, name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise CloudJudgeError("NOT_A_NUMBER", f"{name}={value!r}")
    if isinstance(value, str):
        text = value.strip()
        # 容忍「0.42 分」这种尾巴，但不猜单位
        try:
            value = float(text)
        except ValueError as exc:
            raise CloudJudgeError("NOT_A_NUMBER", f"{name}={text[:40]!r}") from exc
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        raise CloudJudgeError("NOT_A_NUMBER", f"{name}={value!r}")
    if not math.isfinite(number):
        raise CloudJudgeError("NOT_FINITE", name)
    if number < 0.0 or number > 1.0:
        raise CloudJudgeError("OUT_OF_RANGE", f"{name}={number}")
    return round(number, 4)


def _parse_form(value: Any) -> dict[str, float]:
    """形态权重：五个标签缺一不可；缺失是坏输出，不是 0 权重。"""
    if isinstance(value, list):
        converted: dict[str, Any] = {}
        for entry in value:
            if not isinstance(entry, dict):
                raise CloudJudgeError("BAD_FORM", "列表元素不是对象")
            label = entry.get("label") or entry.get("name") or entry.get("form")
            weight = entry.get("score", entry.get("weight"))
            if label is None or weight is None:
                raise CloudJudgeError("BAD_FORM", str(entry)[:80])
            converted[str(label)] = weight
        value = converted
    if not isinstance(value, dict):
        raise CloudJudgeError("BAD_FORM", type(value).__name__)
    normalized = {str(key).strip().upper(): weight for key, weight in value.items()}
    missing = [label for label in FORM_LABELS if label not in normalized]
    if missing:
        raise CloudJudgeError("FORM_MISSING_LABELS", ",".join(missing))
    weights: dict[str, float] = {}
    for label in FORM_LABELS:
        weight = normalized[label]
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise CloudJudgeError("BAD_FORM_WEIGHT", f"{label}={weight!r}")
        number = float(weight)
        if not math.isfinite(number) or number < 0.0:
            raise CloudJudgeError("BAD_FORM_WEIGHT", f"{label}={number}")
        weights[label] = number
    total = sum(weights.values())
    if total <= 0.0:
        raise CloudJudgeError("FORM_ALL_ZERO", "")
    # 归一化是必要的：模型经常给出 0.7/0.2/0.1/0/0 之外的和，例如 2/1/0/0/0。
    return {label: round(weight / total, 4) for label, weight in weights.items()}


def parse_output(content: str, specs: list[Any]) -> tuple[dict[str, Any], str]:
    """把模型输出解析成与本地 provider 同形的 `scores`。"""
    payload = _loads(content)
    nested = payload.get("scores") or payload.get("questions") or payload.get("answers")
    body = nested if isinstance(nested, dict) else payload
    if not isinstance(body, dict):
        raise CloudJudgeError("BAD_SCORES", type(body).__name__)

    scores: dict[str, Any] = {}
    missing: list[str] = []
    for name in noul_names(specs):
        if name not in body:
            missing.append(name)
            continue
        scores[name] = {"noul": _as_unit(body[name], name)}
    if missing:
        raise CloudJudgeError("MISSING_KEYS", ",".join(missing))

    form_raw = payload.get("form", body.get("form"))
    if form_raw is None:
        raise CloudJudgeError("MISSING_KEYS", "form")
    scores["form"] = {"probabilities": _parse_form(form_raw)}

    note_raw = payload.get("note") or body.get("note") or ""
    note = str(note_raw).strip()[:_MAX_NOTE]
    return scores, note


# ---------------------------------------------------------------- HTTP


def resolve_api_key() -> str:
    """与翻译、每日 runner 共用同一套凭据：环境变量优先，其次 ~/.dsh/.credentials.yaml。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        from tools.run_daily import _api_key

        return _api_key()
    except SystemExit as exc:  # run_daily 用 SystemExit 报缺凭据
        raise CloudJudgeError("MISSING_CREDENTIAL", str(exc)) from exc


def _post(messages: list[dict[str, str]], *, model: str, api_key: str, api_url: str,
          timeout: float, thinking: str) -> tuple[str, dict[str, int]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    if thinking == "disabled":
        # 与翻译一致：测量不需要推理链，思考 token 也按输出计费。
        payload["thinking"] = {"type": "disabled"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise CloudJudgeError(f"HTTP_{exc.code}", detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CloudJudgeError("NETWORK", str(exc)) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CloudJudgeError("BAD_RESPONSE_JSON", raw[:200]) from exc
    choices = data.get("choices") or []
    if not choices:
        raise CloudJudgeError("NO_CHOICES", raw[:200])
    content = str((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise CloudJudgeError("EMPTY_RESPONSE", "")
    usage = data.get("usage") or {}
    clean_usage = {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }
    return content, clean_usage


def _retryable(error: CloudJudgeError) -> bool:
    """400/401/403 是配置问题，重试只会烧钱；429/5xx/网络/坏输出值得再试。"""
    if error.code.startswith("HTTP_"):
        suffix = error.code[5:]
        return not (suffix.startswith("4") and suffix != "429")
    return True


def judge(
    specs: list[Any],
    title: str,
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    api_key: str,
    api_url: str = API_URL,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    thinking: str = "disabled",
) -> JudgeOutput:
    """测量一条内容。失败抛 CloudJudgeError；重试次数用尽前不会返回半成品。"""
    messages = build_messages(specs, title, text)
    started = time.time()
    attempts = 0
    last: CloudJudgeError | None = None
    for attempt in range(1, max(0, retries) + 2):
        attempts = attempt
        try:
            content, usage = _post(
                messages,
                model=model,
                api_key=api_key,
                api_url=api_url,
                timeout=timeout,
                thinking=thinking,
            )
        except CloudJudgeError as exc:
            last = exc
            if not _retryable(exc) or attempt > retries:
                raise
            time.sleep(1.5 * attempt)
            continue
        try:
            scores, note = parse_output(content, specs)
        except CloudJudgeError as exc:
            last = exc
            if attempt > retries:
                raise
            # 让模型看着自己的坏输出改，通常比重新生成更稳。
            messages = build_messages(specs, title, text, repair=(content, exc))
            time.sleep(0.8 * attempt)
            continue
        return JudgeOutput(
            scores=scores,
            note=note,
            usage=usage,
            attempts=attempts,
            ms=round((time.time() - started) * 1000),
        )
    raise last or CloudJudgeError("UNKNOWN_FAILURE", "")


# ---------------------------------------------------------------- 批量


def measure_batch(
    items: list[dict[str, Any]],
    specs: list[Any],
    prepared: dict[str, dict[str, Any]],
    *,
    model: str = DEFAULT_MODEL,
    api_key: str,
    api_url: str = API_URL,
    workers: int = DEFAULT_WORKERS,
    retries: int = DEFAULT_RETRIES,
    timeout: float = DEFAULT_TIMEOUT,
    thinking: str = "disabled",
    on_done: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """并发测量一批内容，返回与输入同序的结果。

    每条结果要么带 `scores`，要么带 `error`，不会两者都没有——
    失败必须显式，才能让调用方决定是停下还是带着覆盖缺口继续。
    """
    results: list[dict[str, Any] | None] = [None] * len(items)
    workers = max(1, int(workers))

    def one(index: int, item: dict[str, Any]) -> dict[str, Any]:
        prepared_text = (prepared.get(item["item_id"]) or {}).get("text") or ""
        title = (item.get("title") or "").split(" | ")[0].strip()
        try:
            output = judge(
                specs,
                title,
                prepared_text,
                model=model,
                api_key=api_key,
                api_url=api_url,
                timeout=timeout,
                retries=retries,
                thinking=thinking,
            )
        except CloudJudgeError as exc:
            return {"item_id": item["item_id"], "error": {"code": exc.code, "detail": exc.detail}}
        except Exception as exc:  # noqa: BLE001 - 适配器边界：任何意外都变成可记录的失败
            return {
                "item_id": item["item_id"],
                "error": {"code": "ADAPTER_ERROR", "detail": str(exc)[:300]},
            }
        return {
            "item_id": item["item_id"],
            "scores": output.scores,
            "note": output.note,
            "usage": output.usage,
            "attempts": output.attempts,
            "ms": output.ms,
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(one, index, item): index for index, item in enumerate(items)
        }
        done = 0
        for future in as_completed(futures):
            index = futures[future]
            result = future.result()
            results[index] = result
            done += 1
            if on_done:
                on_done(done, len(items), result)
    return [row for row in results if row is not None]


def usage_totals(results: Iterable[dict[str, Any]]) -> dict[str, int]:
    prompt = completion = 0
    for row in results:
        usage = row.get("usage") or {}
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}
