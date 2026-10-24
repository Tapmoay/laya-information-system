"""从原始 HTML 里取文章配图。

为什么需要：艺术/设计类的判断，光看文字是不够的——排版、封面、装置一眼能看出好坏，
而文字摘要只能转述。抓取时我们把原始 HTML 存下来了，配图信息一直在里面，只是之前没用。
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
SRC_ATTR = re.compile(r"\b(?:src|data-src|data-original)\s*=\s*[\"']([^\"']+)[\"']", re.I)
META_IMAGE = re.compile(
    r"<meta[^>]+(?:property|name)\s*=\s*[\"'](?:og:image|og:image:secure_url|twitter:image)[\"']"
    r"[^>]*content\s*=\s*[\"']([^\"']+)[\"']",
    re.I,
)
META_IMAGE_REV = re.compile(
    r"<meta[^>]+content\s*=\s*[\"']([^\"']+)[\"'][^>]*"
    r"(?:property|name)\s*=\s*[\"'](?:og:image|og:image:secure_url|twitter:image)[\"']",
    re.I,
)
BAD_HINTS = ("logo", "icon", "avatar", "sprite", "qrcode", "qr_", "badge", "pixel", "spacer", "blank")
GOOD_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".avif")


def _looks_like_icon(url: str) -> bool:
    lowered = url.lower()
    if any(hint in lowered for hint in BAD_HINTS):
        return True
    path = urlsplit(lowered).path
    if path.endswith(".svg"):
        return True
    return not path.endswith(GOOD_SUFFIXES) and "?" not in path


def extract_images(html: str, base_url: str, *, limit: int = 3) -> list[str]:
    """优先 og:image，其次正文里第一张像样的图。顺序稳定，便于复现。"""
    found: list[str] = []
    for pattern in (META_IMAGE, META_IMAGE_REV):
        for match in pattern.finditer(html or ""):
            candidate = match.group(1).strip()
            if candidate and candidate not in found:
                found.append(candidate)
    for tag in IMG_RE.findall(html or ""):
        match = SRC_ATTR.search(tag)
        if not match:
            continue
        candidate = match.group(1).strip()
        if candidate and candidate not in found:
            found.append(candidate)

    result: list[str] = []
    for candidate in found:
        absolute = urljoin(base_url, candidate)
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if _looks_like_icon(absolute):
            continue
        if absolute not in result:
            result.append(absolute)
        if len(result) >= limit:
            break
    return result
