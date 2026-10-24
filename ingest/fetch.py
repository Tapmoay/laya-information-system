"""受控抓取（CAP-INF-02）。

约束（都是硬约束，不是建议）：
- 只抓白名单主机；白名单为空时拒绝一切（默认拒绝，而不是默认允许）。
- 尊重 robots.txt；被禁止时记 ACCESS_DISALLOWED，不重试。
- 有限重试、有限字节、显式超时；失败必须变成可审计的 ERROR。
"""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from ingest.charset import sniff_charset
from ingest.html_extract import extract
from ingest.urls import host_of
from tools.validate_objects import validate

ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


class FetchError(RuntimeError):
    pass


@dataclass
class FetchPolicy:
    allowed_hosts: tuple[str, ...] = ()
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    max_bytes: int = 2_000_000
    user_agent: str = "LaYA-personal-information/0.1"
    respect_robots: bool = True
    retry_backoff_seconds: float = 0.05
    store_dir: str | None = None
    # 代理是机器属性，不是来源属性：同一个源在能直连的网络上不需要它。
    # 所以它不写进登记表，而是由命令行 --proxy 或环境变量 LAYA_PROXY 给。
    proxy: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FetchPolicy":
        if "sources" in data:
            data = data["sources"]
        allowed = {"allowed_hosts", "timeout_seconds", "max_attempts", "max_bytes", "user_agent", "respect_robots", "retry_backoff_seconds", "store_dir", "proxy"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"未知的抓取配置项：{sorted(unknown)}")
        hosts = tuple(str(host).lower() for host in data.get("allowed_hosts", []))
        return cls(
            allowed_hosts=hosts,
            timeout_seconds=float(data.get("timeout_seconds", 10.0)),
            max_attempts=int(data.get("max_attempts", 3)),
            max_bytes=int(data.get("max_bytes", 2_000_000)),
            user_agent=str(data.get("user_agent", "LaYA-personal-information/0.1")),
            respect_robots=bool(data.get("respect_robots", True)),
            retry_backoff_seconds=float(data.get("retry_backoff_seconds", 0.05)),
            store_dir=data.get("store_dir"),
            proxy=data.get("proxy"),
        )


def _host_allowed(host: str, policy: FetchPolicy) -> bool:
    if not host:
        return False
    for allowed in policy.allowed_hosts:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _opener(policy: FetchPolicy):
    """没有配代理时保持原来的 urlopen（会尊重系统环境里的代理设置）。

    配了代理就走显式 ProxyHandler：直连被 TLS 阻断的站（实测 projectzero.google、
    deepmind.google、huggingface.co）只能靠这一条路进来，而"抓不到"和"内容不好"
    是两件事，前者不该被记成后者。
    """
    if not policy.proxy:
        return urllib.request.urlopen
    handler = urllib.request.ProxyHandler({"http": policy.proxy, "https": policy.proxy})
    return urllib.request.build_opener(handler).open


def _robots_for(scheme: str, netloc: str, policy: FetchPolicy) -> RobotFileParser | None:
    """netloc 必须带端口：省略端口会去请求 80 端口，robots 静默失效。"""
    # 缓存键带上代理：换代理等于换出口，robots 结论不能跨出口复用。
    key = netloc + "|" + (policy.proxy or "")
    if key in ROBOTS_CACHE:
        return ROBOTS_CACHE[key]
    parser: RobotFileParser | None = None
    try:
        request = urllib.request.Request(
            f"{scheme}://{netloc}/robots.txt", headers={"User-Agent": policy.user_agent}
        )
        with _opener(policy)(request, timeout=policy.timeout_seconds) as response:
            body = response.read(200_000).decode("utf-8", errors="replace")
        parser = RobotFileParser()
        parser.parse(body.splitlines())
    except Exception:  # robots 不可读时不阻塞：记不到规则就等于没有规则
        parser = None
    ROBOTS_CACHE[key] = parser
    return parser


def _error_result(
    *,
    request_id: str,
    requested_url: str,
    final_url: str,
    fetched_at: datetime,
    reason: str,
    status_code: int | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    result = {
        "request_id": request_id,
        "requested_url": requested_url,
        "final_url": final_url or requested_url,
        "outcome": "ERROR",
        "status_code": status_code,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "body_ref": None,
        "error": {
            "status": "ERROR",
            "reason_code": reason,
            **({"detail_ref": detail} if detail else {}),
        },
        "contract_version": "1.0",
    }
    problems = validate("fetch-result", result)
    if problems:
        raise FetchError("抓取结果违反契约：" + "; ".join(problems))
    return result


def _fetch_page(
    url: str,
    *,
    request_id: str,
    policy: FetchPolicy,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str | None]:
    now = now or datetime.now(timezone.utc)
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = parts.netloc.lower()
    scheme = parts.scheme.lower()

    if not _host_allowed(host, policy):
        return _error_result(
            request_id=request_id,
            requested_url=url,
            final_url=url,
            fetched_at=now,
            reason="ACCESS_DISALLOWED",
            detail=f"store/policy/allowlist#/{host}",
        ), None

    if policy.respect_robots:
        robots = _robots_for(scheme, netloc, policy)
        if robots is not None and not robots.can_fetch(policy.user_agent, url):
            return _error_result(
                request_id=request_id,
                requested_url=url,
                final_url=url,
                fetched_at=now,
                reason="ACCESS_DISALLOWED",
                detail=f"store/policy/robots#/{host}",
            ), None

    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": policy.user_agent,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
            with _opener(policy)(request, timeout=policy.timeout_seconds) as response:
                final_url = response.geturl()
                if not _host_allowed(host_of(final_url), policy):
                    return _error_result(
                        request_id=request_id,
                        requested_url=url,
                        final_url=final_url,
                        fetched_at=now,
                        reason="ACCESS_DISALLOWED",
                        status_code=getattr(response, "status", None),
                        detail="store/policy/allowlist#/redirect",
                    ), None
                # 按解码后的大小截断，而不是按压缩包大小：
                # 压缩率高的页面如果按压缩字节截断，会得到半个 gzip 流、解码直接失败。
                encoding = (response.headers.get("Content-Encoding") or "").strip().lower()
                limit = policy.max_bytes + 1
                try:
                    if encoding == "gzip":
                        with gzip.GzipFile(fileobj=response) as stream:
                            raw = stream.read(limit)
                    elif encoding == "deflate":
                        decompressor = zlib.decompressobj()
                        raw = decompressor.decompress(response.read(policy.max_bytes * 8), limit)
                    elif encoding in {"", "identity"}:
                        raw = response.read(limit)
                    else:
                        return _error_result(
                            request_id=request_id,
                            requested_url=url,
                            final_url=url,
                            fetched_at=now,
                            reason="SOURCE_UNREACHABLE",
                            status_code=getattr(response, "status", None),
                            detail=f"trace/{request_id}/unsupported-encoding",
                        ), None
                except (OSError, zlib.error, EOFError):
                    return _error_result(
                        request_id=request_id,
                        requested_url=url,
                        final_url=url,
                        fetched_at=now,
                        reason="SOURCE_UNREACHABLE",
                        status_code=getattr(response, "status", None),
                        detail=f"trace/{request_id}/decode-error",
                    ), None
                truncated = len(raw) > policy.max_bytes
                raw = raw[: policy.max_bytes]
                charset = sniff_charset(raw, response.headers.get_content_charset())
                html = raw.decode(charset, errors="replace")
                status_code = getattr(response, "status", None)
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            last_error = exc
            if 500 <= status_code < 600 and attempt < policy.max_attempts:
                time.sleep(policy.retry_backoff_seconds * attempt)
                continue
            if status_code in (401, 402, 403):
                reason = "SOURCE_PAYWALLED"
            elif status_code in (404, 410):
                reason = "SOURCE_GONE"
            else:
                reason = "SOURCE_UNREACHABLE"
            return _error_result(
                request_id=request_id,
                requested_url=url,
                final_url=url,
                fetched_at=now,
                reason=reason,
                status_code=status_code,
            ), None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < policy.max_attempts:
                time.sleep(policy.retry_backoff_seconds * attempt)
                continue
            lowered = str(exc).lower()
            reason = "TIMEOUT" if "timed out" in lowered else "SOURCE_UNREACHABLE"
            # TLS 被阻断和单纯超时要分开：前者换代理就能进，后者换代理也没用。
            tls = "ssl" in lowered or "certificate" in lowered
            return _error_result(
                request_id=request_id,
                requested_url=url,
                final_url=url,
                fetched_at=now,
                reason=reason,
                detail=f"trace/{request_id}/tls-blocked" if tls else None,
            ), None

        fields = extract(html)
        body_ref = None
        if policy.store_dir:
            from pathlib import Path

            store = Path(policy.store_dir)
            store.mkdir(parents=True, exist_ok=True)
            target = store / f"{request_id}.html"
            target.write_text(html, encoding="utf-8")
            body_ref = str(target).replace("\\", "/")
        if body_ref is None:
            body_ref = f"trace/{request_id}/body"

        result = {
            "request_id": request_id,
            "requested_url": url,
            "final_url": final_url,
            "outcome": "PARTIAL_FETCH" if truncated else "FETCHED",
            "status_code": status_code,
            "fetched_at": now.isoformat(timespec="seconds"),
            "body_ref": body_ref,
            "text": fields["text"][:20000],
            "title": fields["title"],
            # 契约 fetch-result/1.0 的 excerpt 上限是 2000 字；部分站点（如 Anthropic）
            # 把整段正文塞进 meta description，不截断会直接违反契约、让整个抓取中断。
            "excerpt": fields["description"][:2000] if fields["description"] else None,
            "published_at_raw": fields["published_at_raw"],
            "declared_language": fields["declared_language"],
            # 相对 canonical（如 href="/"）会让抓取结果违反契约；这里统一绝对化。
            "canonical_link": urljoin(final_url, fields["canonical_link"]) if fields["canonical_link"] else None,
            "author_name": fields["author_name"],
            "site_name": fields["site_name"],
            "error": None,
            "contract_version": "1.0",
        }
        problems = validate("fetch-result", result)
        if problems:
            raise FetchError("抓取结果违反契约：" + "; ".join(problems))
        return result, html

    raise FetchError(f"抓取失败：{last_error}")


def fetch(
    url: str,
    *,
    request_id: str,
    policy: FetchPolicy,
    now: datetime | None = None,
) -> dict[str, Any]:
    """抓一页并返回 FetchResult 契约对象。"""
    return _fetch_page(url, request_id=request_id, policy=policy, now=now)[0]


def fetch_page(
    url: str,
    *,
    request_id: str,
    policy: FetchPolicy,
    now: datetime | None = None,
) -> dict[str, Any]:
    """抓一页，同时带回原始 HTML。

    链接发现只能对 HTML 原文做：抽好的正文里没有 <a>，拿正文去找链接必然一个都找不到。
    """
    result, html = _fetch_page(url, request_id=request_id, policy=policy, now=now)
    return {"result": result, "html": html}
