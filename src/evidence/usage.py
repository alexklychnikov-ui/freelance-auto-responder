from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from src.evidence.models import EvidenceBundle, EvidenceFact, EvidenceSource
from src.evidence.specificity import detect_generic_action_opener

_HTTP_URL_RE = re.compile(r"https?://[^\s<>\")\]]+", re.I)
def _inspect_near_host_pattern(host: str) -> str:
    """Host must be the inspect object — not merely nearby in the sentence."""
    escaped = re.escape(host)
    site = r"(?:сайт|страницу|ресурс|портал|выдачу|источник)\s+"
    prep = r"(?:на\s+|в\s+|по\s+)?"
    return (
        rf"(?:я\s+)?(?:открыл|проверил|изучил|посмотрел|просмотрел)\w*"
        rf"\s+(?:{site})?{prep}{escaped}"
        r"|"
        rf"{escaped}\s+(?:я\s+)?(?:открыл|проверил|изучил|посмотрел|просмотрел)\w*"
    )


def _as_bundle(bundle: EvidenceBundle | dict[str, Any] | None) -> EvidenceBundle | None:
    if bundle is None:
        return None
    if isinstance(bundle, EvidenceBundle):
        return bundle
    if isinstance(bundle, dict):
        try:
            return EvidenceBundle.model_validate(bundle)
        except Exception:
            return None
    return None


def eligible_facts(bundle: EvidenceBundle | dict[str, Any] | None) -> list[EvidenceFact]:
    parsed = _as_bundle(bundle)
    if parsed is None:
        return []
    return [f for f in parsed.facts if f.eligible_for_response]


_MIN_ANCHOR_LEN = 4


def eligible_anchors(bundle: EvidenceBundle | dict[str, Any] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for fact in eligible_facts(bundle):
        for anchor in fact.anchors:
            key = anchor.casefold().strip()
            if len(key) < _MIN_ANCHOR_LEN or key in seen:
                continue
            seen.add(key)
            out.append(anchor)
    return out


def _source_hosts(source: EvidenceSource) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    for ref in (source.input_ref, source.final_url):
        if not ref:
            continue
        raw = ref.strip()
        host: str | None = None
        if "://" in raw:
            try:
                host = urlparse(raw).hostname
            except Exception:
                host = None
        else:
            host = raw.split("/", 1)[0].strip() or None
        if not host:
            continue
        key = host.casefold()
        if key.startswith("www."):
            key = key[4:]
        if key in seen:
            continue
        seen.add(key)
        hosts.append(key)
    return hosts


def _claims_inspected_host(text: str, host: str) -> bool:
    host_cf = host.casefold()
    if host_cf not in text.casefold():
        return False
    return bool(
        re.search(_inspect_near_host_pattern(host_cf), text, flags=re.I | re.S)
    )


def compact_verified_evidence(
    bundle: EvidenceBundle | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Compact JSON for Draft/Logic/Expert — eligible facts only, no HTML/long quotes."""
    parsed = _as_bundle(bundle)
    if parsed is None:
        return None
    facts = eligible_facts(parsed)
    if not facts:
        return None
    return {
        "facts": [
            {
                "claim": f.claim,
                "anchors": list(f.anchors),
                "relevance": f.relevance,
            }
            for f in facts
        ],
        "insights": [
            {
                "finding": i.finding,
                "implementation_consequence": i.implementation_consequence,
                "kind": i.kind,
                "fact_ids": list(i.fact_ids),
            }
            for i in parsed.insights
        ],
        "sources": [
            {
                "id": s.id,
                "role": s.role,
                "status": s.status,
                "input_ref": s.input_ref,
            }
            for s in parsed.sources
        ],
    }


def evidence_usage_issues(
    response: str,
    bundle: EvidenceBundle | dict[str, Any] | None,
) -> list[str]:
    """Deterministic gates: when eligible facts exist, response must use them."""
    parsed = _as_bundle(bundle)
    if parsed is None:
        return []
    facts = eligible_facts(parsed)
    if not facts:
        return []

    issues: list[str] = []
    text = response or ""

    opener = detect_generic_action_opener(text)
    if opener is not None:
        issues.append(f"evidence:generic_opener:{opener}")

    anchors = eligible_anchors(parsed)
    if anchors:
        haystack = text.casefold()
        used = [a for a in anchors if a.casefold() in haystack]
        need = min(2, len(anchors))
        if len(used) < need:
            if not used:
                issues.append("evidence:no_anchors_used")
            else:
                issues.append("evidence:insufficient_anchors")

    for source in parsed.sources:
        if source.status == "verified":
            continue
        for host in _source_hosts(source):
            if _claims_inspected_host(text, host):
                issues.append(f"evidence:unverified_inspect:{host}")

    if _HTTP_URL_RE.search(text):
        issues.append("evidence:external_url")

    return issues


class EvidenceUsageValidator:
    """Optional thin wrapper — prefer pipeline gate over a new GPT call."""

    def issues(
        self,
        response: str,
        bundle: EvidenceBundle | dict[str, Any] | None,
    ) -> list[str]:
        return evidence_usage_issues(response, bundle)
