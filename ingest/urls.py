"""URL 规范化。

确定性代码必须处理的部分：同一篇文章的十种写法要收敛成一个键，
否则去重与"多转载只算一个证据"都无从谈起。
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = {
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src", "spm", "yclid",
}
TRACKING_PREFIXES = ("utm_",)


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in TRACKING_KEYS or lowered.startswith(TRACKING_PREFIXES)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"只接受 http(s) URL：{url}")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError(f"URL 缺少主机名：{url}")

    port = parts.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")

    pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(key)]
    pairs.sort()
    query = urlencode(pairs)

    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
