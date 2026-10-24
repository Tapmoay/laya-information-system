"""把一条内容还原成它陈述的**事实**，再用事实判断两条内容是不是同一件事。

为什么不比标题：同一件事会被不同媒体按各自的表达习惯写成完全不同的句子。
比标题永远只能近似，而"近似"必然带来 A≈B、B≈C、于是 A≈C 的传递性误并。
比事实则是比**它说了什么**——这是在比内容本身，不是在比包装。

一条事实写成四元组：

    主体 | 关系 | 对象 | 时间

例如「DeepSeek 发布 V4.1-Flash-1017」→ 主体=DeepSeek，关系=发布，
对象=V4.1-Flash-1017，时间=2026-10-17。

三条约束：

1. **只写原文明确说的**。抽取器不许推断、不许补全、不许把评论当事实。
   抽不出来就交空集合——空集合表示"这条没给出可陈述的事实"，不是"它没有事实"。
2. **归一化是确定性代码**，不是模型。模型只负责"读出来"，怎么比较由这里决定。
3. **不跨簇链接**。聚类用"与代表簇比较"的贪心归属，不用并查集：
   并查集会让 A-B、B-C 自动变成 A-C，那正是"约等于"最糟糕的后果。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Sequence

MAX_CLAIMS = 6
PLACEHOLDERS = {"", "-", "无", "none", "n/a", "na", "null", "未知", "unknown", "不详"}
SPACES = re.compile(r"\s+")
PUNCT = re.compile(r"[，。；：！？、\"'“”‘’（）()\[\]【】…—]+")

EXTRACT_SYSTEM = (
    "你是事实抽取器。只输出原文明确陈述的事实，不推断、不补全、不评价。"
    "每条事实一行，格式：主体|关系|对象|时间。"
    "时间是事实本身的日期或版本时间，没有就写 -。"
    "对象是数值、名称或结论，不是形容词。"
    "不要抽取作者、发布时间、投票数、阅读量、导航文字、按钮文字。"
    "主体和对象不能相同。"
    "最多六行。如果原文没有可陈述的事实（纯观点、纯抒情、纯推广），只输出 NONE。"
    "\n示例输入：\n标题：Anthropic 发布 Claude 4，上下文窗口扩到 500K\n"
    "正文：Anthropic 今天发布 Claude 4，上下文窗口从 200K 扩到 500K，API 价格不变。\n"
    "示例输出：\nAnthropic|发布|Claude 4|-\nAnthropic|扩展|上下文窗口 500K|-\nAnthropic|保持|API 价格不变|-"
)
EXTRACT_USER = "标题：{title}\n\n正文节选：\n{body}"


@dataclass(frozen=True)
class Claim:
    subject: str
    relation: str
    object: str
    time: str = "-"

    @property
    def key(self) -> str:
        return fact_key(self)


def _norm(text: str) -> str:
    """只做形态归一，**不改大小写**：交付时要原样显示给人看，
    「deepseek 发布 v4.1」读起来不如「DeepSeek 发布 V4.1」。
    大小写只在算键的时候才抹平。"""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = PUNCT.sub(" ", text)
    return SPACES.sub(" ", text).strip()


def fact_key(claim: Claim) -> str:
    """时间不进键：同一天的说法经常一个是「10月17日」一个是「Oct 17」，
    把它算进键会让同一条事实分成两个。时间单独留着给"新进展"判定用。"""
    payload = "|".join(
        [_norm(claim.subject).lower(), _norm(claim.relation).lower(), _norm(claim.object).lower()]
    )
    return "f-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def parse(raw: str) -> list[Claim]:
    """把抽取器的输出解析成事实。格式不对的行直接丢掉，不猜。"""
    claims: list[Claim] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        line = line.strip().strip("-*0123456789.、 ")
        if not line or line.upper().startswith("NONE"):
            continue
        parts = [part.strip() for part in re.split(r"[|｜]", line)]
        if len(parts) < 3:
            continue
        subject, relation, obj = parts[0], parts[1], parts[2]
        time = parts[3] if len(parts) > 3 else "-"
        if _norm(subject) in PLACEHOLDERS or _norm(relation) in PLACEHOLDERS or _norm(obj) in PLACEHOLDERS:
            continue
        if _norm(subject).lower() == _norm(obj).lower():
            continue  # 同义反复不是事实：「X 引入了 X」
        if not _norm(obj) or _norm(obj) in {"一个", "一种", "某", "一些"}:
            continue  # 小模型常吐「X 是 一个」，那不是事实
        if ":" in obj or "：" in obj or obj.startswith("标题"):
            continue  # 把提示词里的字段名当对象抄下来了
        claim = Claim(_norm(subject), _norm(relation), _norm(obj), _norm(time) or "-")
        if claim.key in seen:
            continue
        seen.add(claim.key)
        claims.append(claim)
        if len(claims) >= MAX_CLAIMS:
            break
    return claims


def build_prompt(title: str, body: str, *, chars: int = 1200) -> tuple[str, str]:
    """喂给模型的是**已经挑过的段落**，不是整页正文。

    整页正文里混着作者、日期、投票数、导航，模型会把这些也当事实抽出来——
    实测抽出过「JGC|在|Oct 16」「667|+|659」这种。挑段落的活交给
    judgment.text.summarise（确定性代码），模型只负责读明白它说了什么。
    """
    from judgment.text import summarise

    clean = summarise(body or "", title or "", chars)
    return EXTRACT_SYSTEM, EXTRACT_USER.format(title=title or "", body=clean or (body or "")[:chars])


def fingerprint(claims: Sequence[Claim]) -> str:
    if not claims:
        return ""
    joined = "|".join(sorted(claim.key for claim in claims))
    return "fx-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def overlap(left: Sequence[Claim], right: Sequence[Claim]) -> tuple[bool, dict[str, Any]]:
    """判定两条内容是不是同一件事。

    够不够合并看**较小的那一组**：短消息只讲一件事，长报道讲五件事，
    只要那件事对上了，它们说的就是同一件事。
    但较小的一组如果有两件以上事实，就要求至少对上两件——
    一件巧合的重合太容易发生了。
    """
    lk = {claim.key for claim in left}
    rk = {claim.key for claim in right}
    if not lk or not rk:
        return False, {"shared": 0, "ratio": 0.0, "reason": "NO_FACTS"}
    shared = lk & rk
    smaller = min(len(lk), len(rk))
    ratio = len(shared) / smaller
    need = 1 if smaller == 1 else 2
    ok = len(shared) >= need and ratio >= 0.5
    return ok, {"shared": len(shared), "ratio": round(ratio, 3), "need": need}


def cluster(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """按事实聚簇：每来一条，只和已有簇的**代表**比，不和其他成员比。

    这样 A 与 B 同、B 与 C 同，不会自动推出 A 与 C 同——
    只有当 A、C 自己都够得上代表时才会进同一簇。
    """
    ordered = sorted((item for item in items if item.get("item_id")), key=lambda item: item["item_id"])
    clusters: list[dict[str, Any]] = []
    assignment: dict[str, Any] = {}

    for item in ordered:
        claims = item.get("claims") or []
        best = None
        for cluster in clusters:
            ok, info = overlap(claims, cluster["claims"])
            if ok and (best is None or info["ratio"] > best[1]["ratio"]):
                best = (cluster, info)
        if best is None:
            cluster = {
                "cluster_id": "fac-" + str(len(clusters) + 1).zfill(3),
                "claims": list(claims),
                "members": [item["item_id"]],
                "representative": item["item_id"],
            }
            clusters.append(cluster)
            assignment[item["item_id"]] = {
                "fact_cluster_id": cluster["cluster_id"],
                "fact_size": 1,
                "fact_role": "REPRESENTATIVE",
                "fact_of": None,
                "fact_count": len(claims),
            }
        else:
            cluster, info = best
            cluster["members"].append(item["item_id"])
            assignment[item["item_id"]] = {
                "fact_cluster_id": cluster["cluster_id"],
                "fact_size": len(cluster["members"]),
                "fact_role": "MEMBER",
                "fact_of": cluster["representative"],
                "fact_count": len(claims),
                "fact_shared": info["shared"],
            }

    # 第二轮补齐 event_size：第一轮遍历时后面的条目还没加进来
    for info in assignment.values():
        cluster = next(c for c in clusters if c["cluster_id"] == info["fact_cluster_id"])
        info["fact_size"] = len(cluster["members"])
    return assignment


def claims_from_raw(raw: str) -> list[Claim]:
    return parse(raw)


def extract(
    item: dict[str, Any],
    *,
    generate: Callable[[str, str], str],
) -> list[Claim]:
    """用调用方给的生成函数抽事实。传函数而不是传模型，测试才能不打 GPU。"""
    system, user = build_prompt(item.get("title") or "", item.get("text_clean") or item.get("text") or "")
    return parse(generate(system, user))
