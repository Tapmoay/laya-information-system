"""喂给判断模型的正文：去掉站点导航，按句子边界截断。

把正文长度固定下来，是因为实测发现截断位置会显著改变质量分数
（同一篇长文，2200 字处截断得到 0.53，3000 字处得到 0.80）。
"""

from __future__ import annotations

import re

MAX_TEXT_CHARS = 3500
SENTENCE_ENDINGS = ("。", "！", "？", "\n", ".", "!", "?")


def strip_site_chrome(text: str, title: str = "") -> str:
    """从标题第一次出现处开始，丢掉前面的导航与图片标签。"""
    if title:
        marker = title[:12]
        index = text.find(marker)
        if index > 0:
            text = text[index:]
    return text.replace("发自 凹非寺", "").strip()


BOILERPLATE_MARKERS = (
    "扫码关注",
    "微信扫码分享",
    "点击下方按钮",
    "利益相关声明",
    "作者与文中产品",
    "关注 少数派",
    "主作者",
    "联合作者",
    "举报",
    "阅读完需",
    "本文字数",
)

# 给"人"看的摘要要额外丢掉的东西：这些行在单个页面里出现一次，
# 跨页重复检测抓不到（公司名/日期不同），但人一眼就知道它们不是内容。
SHAPE_MARKERS = (
    "项目标签",
    "感谢",
    "予gooood分享",
    "Appreciations towards",
    "更多关于他们",
    "Sign in",
    "Subscribe",
    "责任编辑",
    "分享到",
    "读完需",
    "min read",
)


def drop_boilerplate(text: str) -> str:
    """少数派/机核这类页面在标题后面还有一大段作者简介与分享按钮。

    做法：先按行去掉明显的模板行，再从第一段长度 ≥ 40 的行开始取正文。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    kept = [line for line in lines if not any(marker in line for marker in BOILERPLATE_MARKERS)]
    joiner = chr(10)
    for index, line in enumerate(kept):
        if len(line) >= 40:
            return joiner.join(kept[index:])
    return joiner.join(kept)


def truncate_at_sentence(text: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    """超长时在最近一句结束处截断，避免把一句话拦腰切断。"""
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    cut = max(window.rfind(ending) for ending in SENTENCE_ENDINGS)
    if cut < limit * 0.5:
        return window, True
    return text[: cut + 1], True


def prepare(text: str, title: str = "", limit: int = MAX_TEXT_CHARS) -> dict:
    cleaned = drop_boilerplate(strip_site_chrome(text or "", title))
    body, truncated = truncate_at_sentence(cleaned, limit)
    return {"text": body, "truncated": truncated, "length": len(body)}


METADATA_MARKERS = (
    "添加于",
    "发布于",
    "发布时间",
    "来源：",
    "作者：",
    "作者|",
    "浏览量",
    "全文",
    "字 00:",
    "转载",
    "原创",
    "阅读",
)


HEADER_KEYS = ("日期", "作者", "预计阅读时间", "阅读时间", "阅读时长", "发布时间", "更新时间", "来源", "标签", "字数")
HEADER_SEGMENT = re.compile(
    r"^\s*(?:" + "|".join(HEADER_KEYS) + r")\s*[：:]\s*[^|｜]{0,40}[|｜]\s*"
)
FILLER_PREFIX = re.compile(r"^\s*(?:最新发表|最新出版物|最新发布|刚刚发布|发布于|发表于)\s*[：:，,]?\s*")


def strip_leading_meta(line: str) -> str:
    """「日期：… | 预计阅读时间：31分钟 | 作者：某某」这种页头不是内容。

    只在**行首**反复剥，剥到不是页头为止；正文里出现的「作者：」不动。
    """
    text = (line or "").strip()
    while True:
        stripped = HEADER_SEGMENT.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped.strip()
    text = FILLER_PREFIX.sub("", text, count=1).strip()
    # 只剩「作者：某某」这种没有正文的尾巴，整行都不是内容
    if any(text.startswith(key) for key in HEADER_KEYS) and len(text) <= 24:
        return ""
    return text


NON_WORD = re.compile(r"[\s\d:./|·\-—%]+")


def _looks_like_metadata(line: str) -> bool:
    """「2026.10.18 12:20 00:00 / 08:59」这种行几乎没有文字，只有时间与分隔符。"""
    if len(line) >= 40:
        return False
    return len(NON_WORD.sub("", line)) < len(line) * 0.4


def excerpt(text: str, title: str = "", limit: int = 320) -> str:
    """给"人"看的摘要：跳过标题重复与署名行，取开头两三句。

    和 prepare() 的区别：那个是喂模型的（要求确定性、固定长度），
    这个是给人做判断用的（要求可读、信息密度高）。两者不该混用。
    """
    body = strip_site_chrome(text or "", title)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    kept: list[str] = []
    for line in lines:
        if title and line[:14] == title[:14]:
            continue
        if any(marker in line for marker in METADATA_MARKERS) and len(line) < 60:
            continue
        if any(marker in line for marker in BOILERPLATE_MARKERS):
            continue
        if _looks_like_metadata(line):
            continue
        kept.append(line)
    joined = " ".join(kept).strip()
    if not joined:
        return ""
    if len(joined) <= limit:
        return joined
    window = joined[:limit]
    cut = max(window.rfind(ending) for ending in ("。", "！", "？", "；", ".", "!", "?"))
    if cut >= limit * 0.5:
        return window[: cut + 1]
    return window + "…"


SQUASHED_MARKERS = ("找设计师", "提交项目", "扫码", "联系我们", "商务合作", "广告位", "我要投稿", "App下载")

CJK_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF))
CAPTION_MARKERS = ("▼", "©", "Photo by", "Image courtesy", "All images", "图片", "摄影", "▲")


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    hits = sum(1 for char in text if any(low <= ord(char) <= high for low, high in CJK_RANGES))
    return hits / len(text)


def _usable_line(line: str, title: str, *, want_cjk: bool) -> bool:
    if len(line) < 16:
        return False
    if title and line[:14] == title[:14]:
        return False
    if any(marker in line for marker in SHAPE_MARKERS):
        return False
    if any(marker in line for marker in BOILERPLATE_MARKERS):
        return False
    if any(marker in line for marker in CAPTION_MARKERS) and len(line) < 120:
        return False
    if any(marker in line for marker in METADATA_MARKERS) and len(line) < 60:
        return False
    if _looks_like_metadata(line):
        return False
    # 有的站点把按钮文字拆成单个汉字并加空格（「业 主 找 设 计 师」），先压掉空格再匹配
    squashed = line.replace(" ", "")
    if any(marker in squashed for marker in SQUASHED_MARKERS):
        return False
    # 中英双语的站点（谷德）正文里中文段后面跟一段英文翻译：只要中文那版
    if want_cjk and _cjk_ratio(line) < 0.15:
        return False
    return True


def summarise(text: str, title: str = "", limit: int = 300, *, picks: int = 3) -> str:
    """给人看的摘要：从开头、中段、结尾各取一段，用 … 隔开。

    为什么不是"取前 N 字"：实测开头往往是项目标签、署名、图片说明，
    真正的内容在中段。只截头部会得到"看起来像摘要，其实什么也没说"的东西，
    而标注人正是被这种摘要逼到"无法判断"。
    """
    body = strip_site_chrome(text or "", title)
    want_cjk = _cjk_ratio(title) > 0.2 or _cjk_ratio(body[:600]) > 0.3
    lines = [
        cleaned
        for cleaned in (strip_leading_meta(line) for line in body.splitlines() if line.strip())
        if cleaned and _usable_line(cleaned, title, want_cjk=want_cjk)
    ]
    if not lines:
        return ""
    if len("".join(lines)) <= limit:
        return " ".join(lines)

    chosen: list[int] = []
    total = len(lines)
    for step in range(picks):
        start = int(total * step / max(1, picks))
        # 在窗口里挑最长的一段：小节标题往往比正文短，挑到标题等于没摘要
        window = [index for index in range(start, min(total, start + 3)) if index not in chosen]
        if not window:
            window = [index for index in range(start, total) if index not in chosen][:1]
        if not window:
            break
        chosen.append(max(window, key=lambda index: min(len(lines[index]), 200)))
    parts = [lines[index] for index in sorted(chosen)]
    joined = " … ".join(parts)
    if len(joined) <= limit:
        return joined

    # 超长：逐段累加，到边界为止；最后一段按句切断
    budget = limit
    kept: list[str] = []
    for part in parts:
        room = budget - (2 if kept else 0)
        if room <= 20:
            break
        if len(part) <= room:
            kept.append(part)
            budget -= len(part) + (2 if kept else 0)
            continue
        piece, _ = truncate_at_sentence(part, room)
        kept.append(piece)
        budget = 0
        break
    return " … ".join(kept)
