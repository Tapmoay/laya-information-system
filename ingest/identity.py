"""来源身份链（CAP-INF-04）。

规则只认可指认的证据：
- 页面 canonical link 指向别的主机 → 上游已确认（CONFIRMED）；
- 页面自称"转载自 X"但没有链接 → 上游只是 PROBABLE；
- 两者都没有 → relation = UNKNOWN，不假设"没有链接就是原创"。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PageSignals:
    page_host: str
    site_name: str | None = None
    author_name: str | None = None
    canonical_link_host: str | None = None
    upstream_hint: str | None = None


def resolve_identity(candidate_id: str, signals: PageSignals) -> dict:
    publisher_name = signals.site_name or (signals.page_host or None)
    publisher_status = "PROBABLE" if signals.site_name else ("UNKNOWN" if not signals.page_host else "PROBABLE")
    publisher = {
        "name": publisher_name,
        "identity_status": publisher_status,
        "evidence_refs": ["fetch/site-name"] if signals.site_name else [],
    }

    upstream = None
    relation = "UNKNOWN"
    evidence: list[str] = []

    if signals.canonical_link_host and signals.canonical_link_host != signals.page_host:
        upstream = {
            "name": signals.canonical_link_host,
            "identity_status": "CONFIRMED",
            "evidence_refs": ["fetch/canonical-link"],
        }
        relation = "REPOST"
        evidence = ["fetch/canonical-link"]
    elif signals.upstream_hint:
        upstream = {
            "name": signals.upstream_hint,
            "identity_status": "PROBABLE",
            "evidence_refs": ["fetch/upstream-hint"],
        }
        relation = "REPOST"
        evidence = ["fetch/upstream-hint"]
    elif (
        signals.canonical_link_host
        and signals.canonical_link_host == signals.page_host
        and signals.author_name
    ):
        relation = "ORIGINAL"
        evidence = ["fetch/canonical-link", "fetch/author"]

    evidence = evidence + (publisher["evidence_refs"] or [])
    return {
        "source_identity_id": f"src-{candidate_id}",
        "candidate_id": candidate_id,
        "publisher": publisher,
        "upstream": upstream,
        "relation": relation,
        "evidence_refs": sorted(set(evidence)),
        "contract_version": "1.0",
    }


def identity_key(source_identity: dict) -> str:
    """独立来源的判定键：有上游就按上游算，没有才按发布者算。"""
    upstream = source_identity.get("upstream")
    if upstream and upstream.get("name"):
        return f"upstream:{upstream['name'].lower()}"
    publisher = source_identity.get("publisher") or {}
    if publisher.get("identity_status") != "UNKNOWN" and publisher.get("name"):
        return f"publisher:{publisher['name'].lower()}"
    return f"unknown:{source_identity['candidate_id']}"


def independence_count(source_identities: list[dict]) -> int:
    """多个转载同一上游只算一个证据（policy §5.2）。"""
    return len({identity_key(item) for item in source_identities})
