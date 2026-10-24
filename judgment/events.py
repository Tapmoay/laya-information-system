"""事件去重：把「说的是同一件事」的条目并成一簇。

和 ingest/dedupe.py 的分工：
- dedupe.py 管**确定性重复**——同一个规范化 URL、同一份内容摘要。它不猜。
- 这里管**同一事件的多次报道**——标题被改写过，URL 和正文都不一样。

难点在于中文媒体的同一事件经常连版本号都不写：一条叫「DeepSeek 新模型发布」，
另一条叫「突发！超级性价比斩杀一切」。所以判据不能只看字符串相似，
要看**可锚定的实体**：版本号、专有名词、稀有 CJK 片段。

三条硬约束：
1. **只并跨源**。同一来源内部的近似（系列文章第 59/60/61 期、同一天的两个发布）
   不是重复，是连载。实测把它们并起来全是误判。
2. **精度优先**。并错两条不同的新闻，比漏并一对更糟：用户会以为这两件事是一件事。
   所以阈值取保守值，够不上就分开，并把这些"差一点"的对记下来供人复查。
3. **同一份输入永远得到同一组结果**。不引入随机、不引入模型、不依赖集合遍历顺序。

事件键（event_key）是**条目的纯函数**，不依赖语料：跨天比对时，昨天的条目已经不在
今天的语料里，任何依赖"这个词在本次语料里稀不稀有"的算法都会失效。
"""

from __future__ import annotations

import hashlib
import unicodedata
import re
from typing import Any, Iterable, Sequence

from judgment.embeddings import cross_lingual_match
from judgment.simhash import jaccard_sets, shingle_set

CJK_RUN = re.compile("[\u4e00-\u9fff]{2,}")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9._+\-]*")
NUMV = re.compile(r"\d+(?:\.\d+)+")
# 这些词做锚点等于没有锚点
STOP = set(
    """the and for with from that this are was were not but you can how why what new via its has have will
    more than one two all out now who use our about after also been being between both into just like make
    many most much must need only other over same some such take then there these they thing think time
    very want way well were when where which while work would year your""".split()
)


def _strong(token: str) -> bool:
    """版本号、带数字的型号：可靠性最高的锚点。"""
    return any(ch.isdigit() for ch in token) and len(token) >= 3


def anchors(title: str, body: str = "") -> dict[str, list[str]]:
    """抽出可锚定的实体。标题给 CJK 片段，正文只给前 400 字（再往后是作者与页脚）。"""
    # 中文站点常用全角写型号（Ｖ４．１、ＧＰＴ），不归一就永远认不出是同一个东西。
    title = unicodedata.normalize("NFKC", title or "")
    snippet = unicodedata.normalize("NFKC", body or "")[:400]
    text = title + " " + snippet
    strong: set[str] = set()
    names: set[str] = set()
    for match in LATIN.finditer(text):
        raw = match.group(0)
        # 连写型号要拆开：「DeepSeek-V4.1-Flash-1017」整体是版本号，
        # 但里面的 DeepSeek 才是专有名词。不拆开，两条同一事件的报道就永远对不上。
        for piece in [raw] + [part for part in re.split(r"[-_.]", raw) if part]:
            token = piece.lower().strip("._+-")
            if len(token) < 2 or token in STOP:
                continue
            if _strong(token):
                strong.add(token)
            elif len(token) >= 4 and token.isalpha():
                # 专有名词：原文里带大写（DeepSeek / GPT / MCP），或全大写缩写
                if any(ch.isupper() for ch in piece[1:]) or piece.isupper():
                    names.add(token)
    for match in NUMV.finditer(text):
        if len(match.group(0)) >= 3:
            strong.add(match.group(0))
    cjk: set[str] = set()
    for run in CJK_RUN.findall(title):
        for size in (3, 4):
            for index in range(len(run) - size + 1):
                cjk.add(run[index : index + size])
    # 拉丁语言没有分词问题但有改写问题：「Anthropic expands the X Program」
    # 与「X Program: Anthropic widens access」词面重合很高，靠词集合就能认出来。
    words = {
        match.group(0).lower()
        for match in LATIN.finditer(title)
        if len(match.group(0)) >= 3 and match.group(0).lower() not in STOP
    }
    return {"strong": sorted(strong), "names": sorted(names), "cjk": sorted(cjk), "words": sorted(words)}


def event_key(tokens: dict[str, list[str]]) -> str:
    """稳定的键：只由条目自己决定，和它在哪个语料里无关。"""
    parts = tokens["strong"] + tokens["names"]
    if not parts:
        parts = tokens["cjk"][:3]
    # 没有锚点就没有事件身份。以前这里会对空 parts 做哈希，得到一个所有
    # 无锚点条目共用的 ev-e3b0c44298fc，重复守卫会把任意两条无关内容判成同一事件。
    if not parts:
        return ""
    digest = hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()
    return "ev-" + digest[:12]


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def compare(
    left: dict[str, list[str]],
    right: dict[str, list[str]],
    *,
    rare_cjk: set[str] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """判两簇锚点是不是同一事件，返回 (是否同一事件, 依据)。

    规则写死成五条，每条都对应一种真实形态：
      1. 共同版本号 ≥2          —— 同一个发布、同一篇论文的两个版本号
      2. 共同版本号 ≥1 且专有名词重合 ≥1 —— 「GPT-6 发布」与「GPT-6 表现如何」
      3. 共同专有名词 ≥2 且标题高度相似  —— 「Anthropic 发布 X」与「X 出自 Anthropic」
      4. 稀有 CJK 片段 ≥2 且标题相似     —— 中文媒体不写版本号的情形，靠语料稀有度兜底
      5. 标题词集合 ≥4 个重合且相似度 ≥0.5 —— 英文媒体的改写
      6. 其余一律分开
    """
    strong = set(left["strong"]) & set(right["strong"])
    names = set(left["names"]) & set(right["names"])
    # 纯数字片段（4.1、1017）太弱：一条讲 MiniMax 的报道里顺手提一句
    # DeepSeek-V4.1，就会和真正讲 V4.1 的那条共享 {"4.1"} 与 {"deepseek"}。
    # 实测就这样把两条不同的发布会并成了一条，所以数字片段单独算。
    specific = {token for token in strong if re.search(r"[a-z]", token)}
    numeric = strong - specific
    where: dict[str, Any] = {
        "shared_strong": sorted(strong),
        "shared_names": sorted(names),
        "jaccard": round(_jaccard(left["cjk"], right["cjk"]), 3),
    }
    where["specific"] = sorted(specific)
    if len(specific) >= 2:
        where["rule"] = "SHARED_VERSION"
        return True, where
    if len(specific) >= 1 and len(names) >= 1:
        where["rule"] = "VERSION_AND_NAME"
        return True, where
    if len(numeric) >= 2 and len(names) >= 1:
        where["rule"] = "NUMERIC_AND_NAME"
        return True, where
    if len(names) >= 2 and where["jaccard"] >= 0.30:
        where["rule"] = "NAMES_AND_TITLE"
        return True, where
    if rare_cjk:
        shared_rare = (set(left["cjk"]) & set(right["cjk"])) & rare_cjk
        where["shared_rare_cjk"] = sorted(shared_rare)[:4]
        if len(shared_rare) >= 2 and where["jaccard"] >= 0.35:
            where["rule"] = "RARE_CJK_AND_TITLE"
            return True, where
    shared_words = set(left["words"]) & set(right["words"])
    word_jaccard = _jaccard(left["words"], right["words"])
    where["shared_words"] = len(shared_words)
    where["word_jaccard"] = round(word_jaccard, 3)
    if len(shared_words) >= 4 and word_jaccard >= 0.5:
        where["rule"] = "TITLE_WORDS"
        return True, where
    where["rule"] = "SEPARATE"
    return False, where


def rare_cjk(corpus: Sequence[dict[str, list[str]]], *, max_share: float = 0.05) -> set[str]:
    """语料内稀有的 CJK 片段。

    只在**同一次运行内**使用：它的定义依赖语料，跨天就不成立了。
    跨天比对靠 compare() 去掉这条规则之后的前三条。
    """
    counts: dict[str, int] = {}
    for tokens in corpus:
        for token in tokens["cjk"]:
            counts[token] = counts.get(token, 0) + 1
    cap = max(2, int(len(corpus) * max_share))
    return {token for token, count in counts.items() if count <= cap}


# 实测：同一篇被转发（加编者按）Jaccard ≈ 0.99，删掉两成 ≈ 0.80，
# 而同语料里不相关的两条最高只有 ≈ 0.02。0.55 落在中间很宽的空档里。
NEAR_DUPLICATE_JACCARD = 0.55


def cluster(
    items: Sequence[dict[str, Any]],
    *,
    cross_source_only: bool = True,
    embeddings: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """按事件聚簇：**只和已有簇的代表比**，不和其他成员比。

    不用并查集。并查集会让 A-B、B-C 自动推出 A-C——那正是「约等于」最糟糕的后果。
    代表制下，新条目必须自己够得上代表才进簇，传递链在这里断掉。

    cross_source_only 默认 True，是**没有事实时的保守退路**：同源的近似更可能是
    连载（第 59/60/61 期）。真正的判断在事实层（judgment/facts.py 的 cluster），
    那里没有这条限制——事实不同就自然分开，不需要靠"跨不跨源"来回避。
    """
    prepared: list[dict[str, Any]] = []
    for order, item in enumerate(items):
        if not item.get("item_id"):
            continue  # 坏数据不该让整批聚类崩掉，后面按 id 引用它时才该报错
        body = (item.get("title") or "") + " " + (item.get("text_clean") or item.get("text") or "")
        tokens = anchors(item.get("title") or "", item.get("text_clean") or item.get("text") or "")
        prepared.append(
            {
                "item": item,
                "order": order,
                "tokens": tokens,
                "shingles": shingle_set(body[:2200]),
            }
        )
    prepared.sort(key=lambda row: row["item"]["item_id"])

    rare = rare_cjk([row["tokens"] for row in prepared])
    clusters: list[dict[str, Any]] = []
    membership: dict[str, dict[str, Any]] = {}

    for row in prepared:
        best = None
        for cluster in clusters:
            if cross_source_only and cluster["source"] == row["item"].get("source_id"):
                continue
            same, where = compare(row["tokens"], cluster["tokens"], rare_cjk=rare)
            if not same:
                similarity = jaccard_sets(row["shingles"], cluster["shingles"])
                if similarity >= NEAR_DUPLICATE_JACCARD:
                    same, where = True, {"rule": "NEAR_DUPLICATE_TEXT", "jaccard": round(similarity, 3)}
            if not same and embeddings:
                # 跨语言只能靠向量：两条不同语言的内容，字面重合恒为 0。
                # 同语言不用向量——实测不相关能到 0.889，改写只有 0.68，顺序是反的。
                left_vec = embeddings.get(row["item"]["item_id"])
                right_vec = embeddings.get(cluster["representative"])
                if left_vec and right_vec:
                    matched, similarity = cross_lingual_match(
                        left_vec,
                        row["item"].get("detected_language") or row["item"].get("declared_language"),
                        right_vec,
                        cluster.get("language"),
                    )
                    if matched:
                        same, where = True, {"rule": "CROSS_LINGUAL_EMBEDDING", "cosine": similarity}
            if same:
                best = (cluster, where)
                break
        if best is None:
            cluster = {
                "tokens": row["tokens"],
                "shingles": row["shingles"],
                "source": row["item"].get("source_id"),
                "representative": row["item"]["item_id"],
                "language": row["item"].get("detected_language") or row["item"].get("declared_language"),
                "members": [row["item"]["item_id"]],
            }
            clusters.append(cluster)
            membership[row["item"]["item_id"]] = {"cluster": cluster, "anchors": row["tokens"]}
        else:
            best[0]["members"].append(row["item"]["item_id"])
            membership[row["item"]["item_id"]] = {"cluster": best[0], "anchors": row["tokens"]}

    order_of = {row["item"]["item_id"]: row["order"] for row in prepared}
    score_of = {row["item"]["item_id"]: float(row["item"].get("score") or 0.0) for row in prepared}
    result: dict[str, Any] = {}
    for cluster in clusters:
        representative = sorted(
            cluster["members"], key=lambda item_id: (-score_of[item_id], order_of[item_id])
        )[0]
        cluster_id = "evc-" + hashlib.sha256(
            "|".join(sorted(cluster["members"])).encode("utf-8")
        ).hexdigest()[:12]
        for item_id in cluster["members"]:
            tokens = membership[item_id]["anchors"]
            result[item_id] = {
                "event_cluster_id": cluster_id,
                "event_key": event_key(tokens),
                "event_size": len(cluster["members"]),
                "event_role": "REPRESENTATIVE" if item_id == representative else "MEMBER",
                "event_of": None if item_id == representative else representative,
                "event_anchors": (tokens["strong"] + tokens["names"])[:6],
            }
    return result
