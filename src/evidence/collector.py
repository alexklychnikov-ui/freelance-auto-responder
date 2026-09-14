from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Mapping

from src.analyzer.project_brief import is_site_recon_task
from src.evidence.discovery import (
    ResourceCandidate,
    discover,
    extract_inlined_attachment,
)
from src.evidence.fetcher import SourceContent, fetch_url, fetch_with_browser
from src.evidence.models import EvidenceBundle, EvidenceFact, EvidenceSource
from src.evidence.normalize import normalize_content
from src.evidence.recon import is_recon_target, run_site_recon
from src.evidence.researcher import build_insights, extract_facts_from_text
from src.models import ProjectFull

logger = logging.getLogger(__name__)

_SSRF_REJECT_CODES = frozenset(
    {
        "blocked_scheme",
        "blocked_userinfo",
        "blocked_port",
        "blocked_hostname",
        "blocked_ip",
        "blocked_empty",
        "blocked_parse",
        "blocked_ssrf",
        "dns_failure",
        "unsupported_content_type",
        "not_http_url",
    }
)


def collect_candidates(
    project: ProjectFull,
    *,
    max_urls: int = 3,
) -> list[ResourceCandidate]:
    """Discover research candidates from project text. No network fetch yet."""
    return discover(project, max_urls=max_urls)


def _source_id(input_ref: str) -> str:
    return hashlib.sha256(input_ref.encode("utf-8")).hexdigest()[:16]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def project_content_hash(project: ProjectFull) -> str:
    raw = f"{project.title or ''}\n{project.full_description or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _map_status(content: SourceContent) -> str:
    if content.error_code is None and content.text:
        return "verified"
    err = content.error_code or "unavailable"
    if err.startswith("blocked_") or err in _SSRF_REJECT_CODES:
        return "rejected"
    return "unavailable"


def _resolve_injected_raw(
    candidate: ResourceCandidate,
    injected: Mapping[str, str] | None,
) -> str | None:
    if not injected:
        return None
    if candidate.input_ref in injected:
        return injected[candidate.input_ref]
    # Allow host / bare key match for offline fixtures
    ref = candidate.input_ref.rstrip("/").lower()
    for key, value in injected.items():
        k = key.rstrip("/").lower()
        if k == ref or k in ref or ref in k:
            return value
        # host hint
        hint = (candidate.title_hint or "").lower()
        if hint and hint in k:
            return value
    return None


def _bundle_status(
    *,
    required: bool,
    sources: list[EvidenceSource],
) -> str:
    if not required or not sources:
        return "not_required"
    verified = [s for s in sources if s.status == "verified"]
    failed = [s for s in sources if s.status in ("unavailable", "rejected")]
    if verified and not failed:
        return "complete"
    if verified and failed:
        return "partial"
    if failed and not verified:
        return "failed"
    return "partial"


class EvidenceService:
    """Evidence Research collector: discovery + safe fetch + fact extraction."""

    def collect_candidates(
        self,
        project: ProjectFull,
        *,
        max_urls: int = 3,
    ) -> list[ResourceCandidate]:
        return collect_candidates(project, max_urls=max_urls)

    def fetch_candidate(
        self,
        candidate: ResourceCandidate,
        *,
        timeout: float = 60.0,
        use_browser_fallback: bool = False,
        client=None,
        project_text: str | None = None,
    ) -> EvidenceSource:
        """Fetch one candidate into EvidenceSource. No LLM / extraction."""
        now = datetime.now(timezone.utc)
        base = EvidenceSource(
            id=_source_id(candidate.input_ref),
            kind=candidate.kind,
            role=candidate.role,
            input_ref=candidate.input_ref,
            title=candidate.title_hint,
            fetch_method="attachment" if candidate.kind == "attachment" else "http",
            status="unavailable",
            retrieved_at=now,
        )

        if candidate.kind != "url":
            inlined = extract_inlined_attachment(
                project_text or "", candidate.input_ref
            )
            if inlined:
                return base.model_copy(
                    update={
                        "status": "verified",
                        "error_code": None,
                        "fetch_method": "offline",
                        "content_type": "text/plain",
                        "content_hash": _content_hash(inlined),
                        "retrieved_at": now,
                    }
                )
            return base.model_copy(
                update={
                    "status": "unavailable",
                    "error_code": "attachment_not_inlined",
                    "fetch_method": "attachment",
                }
            )

        content = fetch_url(
            candidate.input_ref,
            timeout=timeout,
            client=client,
        )

        if (
            use_browser_fallback
            and content.error_code
            and content.error_code not in _SSRF_REJECT_CODES
            and content.error_code != "too_large"
        ):
            content = fetch_with_browser(candidate.input_ref, timeout=timeout)

        status = _map_status(content)
        updates: dict = {
            "final_url": content.final_url,
            "http_status": content.status_code,
            "content_type": content.content_type,
            "error_code": content.error_code,
            "fetch_method": content.method,
            "status": status,
            "retrieved_at": now,
        }
        if content.title:
            updates["title"] = content.title
        elif candidate.title_hint:
            updates["title"] = candidate.title_hint
        if content.text:
            updates["content_hash"] = _content_hash(content.text)
        return base.model_copy(update=updates)

    def collect(
        self,
        project: ProjectFull,
        *,
        settings=None,
        timeout: float | None = None,
        max_urls: int | None = None,
        http_client=None,
        fetch: bool = True,
        injected_texts: Mapping[str, str] | None = None,
        use_browser_fallback: bool = False,
        max_facts_per_source: int = 7,
        recon_max_pages: int | None = None,
    ) -> EvidenceBundle:
        """Discover → fetch/inject → extract grounded facts → EvidenceBundle.

        When ``fetch=False``, use ``injected_texts`` (url/host → raw HTML/text)
        for offline tests. No orchestrator wiring.
        """
        t0 = time.perf_counter()
        now = datetime.now(timezone.utc)
        resolved_max = max_urls
        resolved_timeout = timeout
        resolved_recon = recon_max_pages
        if settings is not None:
            if resolved_max is None:
                resolved_max = int(getattr(settings, "evidence_max_urls", 3))
            if resolved_timeout is None:
                resolved_timeout = float(
                    getattr(settings, "evidence_timeout_seconds", 60.0)
                )
            if resolved_recon is None:
                resolved_recon = int(getattr(settings, "evidence_recon_max_pages", 3))
        if resolved_max is None:
            resolved_max = 3
        if resolved_timeout is None:
            resolved_timeout = 60.0
        if resolved_recon is None:
            resolved_recon = 3

        candidates = self.collect_candidates(project, max_urls=resolved_max)
        required = bool(candidates)
        if not required:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "evidence_collect project_id=%s sources=0 statuses={} "
                "facts=0 latency_ms=%s errors=[] status=not_required",
                project.project_id,
                latency_ms,
            )
            return EvidenceBundle(
                status="not_required",
                required=False,
                project_hash=project_content_hash(project),
                collected_at=now,
            )

        sources: list[EvidenceSource] = []
        facts: list[EvidenceFact] = []
        source_texts: dict[str, str] = {}
        warnings: list[str] = []
        recon_targets: list[str] = []

        for candidate in candidates:
            sid = _source_id(candidate.input_ref)
            base = EvidenceSource(
                id=sid,
                kind=candidate.kind,
                role=candidate.role,
                input_ref=candidate.input_ref,
                title=candidate.title_hint,
                fetch_method="attachment" if candidate.kind == "attachment" else "http",
                status="unavailable",
                retrieved_at=now,
            )

            if candidate.kind != "url":
                inlined = extract_inlined_attachment(
                    project.full_description or "", candidate.input_ref
                )
                if not inlined:
                    warnings.append(
                        f"attachment not inlined: {candidate.input_ref}"
                    )
                    continue
                sources.append(
                    base.model_copy(
                        update={
                            "status": "verified",
                            "error_code": None,
                            "fetch_method": "offline",
                            "content_type": "text/plain",
                            "content_hash": _content_hash(inlined),
                            "title": candidate.title_hint or candidate.input_ref,
                        }
                    )
                )
                source_texts[sid] = inlined
                facts.extend(
                    extract_facts_from_text(
                        sid,
                        candidate.role,
                        inlined,
                        max_facts=max_facts_per_source,
                    )
                )
                continue

            text: str | None = None
            title: str | None = candidate.title_hint

            if not fetch:
                raw = _resolve_injected_raw(candidate, injected_texts)
                if raw is None:
                    sources.append(
                        base.model_copy(
                            update={
                                "status": "unavailable",
                                "error_code": "not_fetched",
                            }
                        )
                    )
                    warnings.append(f"no inject for {candidate.input_ref}")
                    continue
                norm = normalize_content(raw, "text/html")
                text = norm.text
                if norm.title:
                    title = norm.title
                sources.append(
                    base.model_copy(
                        update={
                            "status": "verified" if text else "unavailable",
                            "final_url": candidate.input_ref,
                            "content_type": "text/html",
                            "http_status": 200 if text else None,
                            "content_hash": _content_hash(text) if text else None,
                            "title": title,
                            "error_code": None if text else "empty_content",
                            "fetch_method": "http",
                        }
                    )
                )
            else:
                content = fetch_url(
                    candidate.input_ref,
                    timeout=resolved_timeout,
                    client=http_client,
                )
                if (
                    use_browser_fallback
                    and content.error_code
                    and content.error_code not in _SSRF_REJECT_CODES
                    and content.error_code != "too_large"
                ):
                    content = fetch_with_browser(
                        candidate.input_ref,
                        timeout=resolved_timeout,
                    )
                status = _map_status(content)
                updates: dict = {
                    "final_url": content.final_url,
                    "http_status": content.status_code,
                    "content_type": content.content_type,
                    "error_code": content.error_code,
                    "fetch_method": content.method,
                    "status": status,
                }
                if content.title:
                    updates["title"] = content.title
                    title = content.title
                elif candidate.title_hint:
                    updates["title"] = candidate.title_hint
                text = content.text
                if text:
                    updates["content_hash"] = _content_hash(text)
                sources.append(base.model_copy(update=updates))
                if status != "verified":
                    warnings.append(
                        f"fetch failed {candidate.input_ref}: {content.error_code}"
                    )
                elif candidate.role in ("data_source", "unknown"):
                    target = content.final_url or candidate.input_ref
                    if is_recon_target(target):
                        recon_targets.append(target)

            if not text:
                # Unavailable/rejected → no "inspected" facts for this site
                continue

            source_texts[sid] = text
            facts.extend(
                extract_facts_from_text(
                    sid,
                    candidate.role,
                    text,
                    max_facts=max_facts_per_source,
                )
            )

        insights = build_insights(
            project,
            sources,
            facts,
            source_texts=source_texts,
        )

        project_text = f"{project.title or ''}\n{project.full_description or ''}"
        if (
            fetch
            and resolved_recon > 0
            and recon_targets
            and is_site_recon_task(project_text)
        ):
            try:
                recon_timeout = min(float(resolved_timeout), 15.0)
                recon = run_site_recon(
                    recon_targets[0],
                    timeout=recon_timeout,
                    max_pages=resolved_recon,
                    client=http_client,
                )
            except Exception:
                logger.warning("evidence_recon_failed", exc_info=True)
                warnings.append(f"recon failed {recon_targets[0]}")
            else:
                known_ids = {s.id for s in sources}
                for src in recon.sources:
                    if src.id in known_ids:
                        continue
                    known_ids.add(src.id)
                    sources.append(src)
                    source_texts[src.id] = recon.texts.get(src.id, "")
                facts.extend(f for f in recon.facts if f.source_id in known_ids)
                insights.extend(recon.insights)
                warnings.extend(recon.warnings)

        status = _bundle_status(required=required, sources=sources)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        status_counts = Counter(s.status for s in sources)
        err_codes = sorted({s.error_code for s in sources if s.error_code})
        logger.info(
            "evidence_collect project_id=%s sources=%s statuses=%s "
            "facts=%s latency_ms=%s errors=%s status=%s",
            project.project_id,
            len(sources),
            dict(status_counts),
            len(facts),
            latency_ms,
            err_codes,
            status,
        )
        return EvidenceBundle(
            status=status,  # type: ignore[arg-type]
            required=required,
            project_hash=project_content_hash(project),
            sources=sources,
            facts=facts,
            insights=insights,
            warnings=warnings,
            collected_at=now,
        )
