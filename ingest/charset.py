"""字符集嗅探。

单独成模块是因为它必须在"读字节"和"抽字段"之间发生，
而这两步分属 fetch 与 html_extract 两个模块。
"""

from __future__ import annotations

import re

META_CHARSET = re.compile(r'<meta[^>]+charset\s*=\s*["\']?\s*([a-z0-9_\-]+)', re.I)

def sniff_charset(raw: bytes, declared: str | None) -> str:
    """决定用哪个字符集解码。

    很多中文站点（例如 midifan）响应头不带 charset，只在 HTML 里写
    <meta charset=gbk>。此时若一律按 utf-8 解，正文会变成一堆问号，
    而"抓到了但内容不可读"会以"质量分低"的形式悄悄污染标定数据。
    """
    if declared and declared.strip():
        label = declared.strip().lower()
        if label in {"gb2312", "gbk", "gb18030"}:
            return "gb18030"  # gb18030 是 gbk 的超集，用它可以少踩一次解码错误
        return label
    head = raw[:4096].decode("ascii", errors="ignore").lower()
    match = META_CHARSET.search(head)
    if match:
        label = match.group(1).strip().lower()
        if label in {"gb2312", "gbk", "gb18030"}:
            return "gb18030"
        return label
    return "utf-8"
