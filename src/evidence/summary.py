from __future__ import annotations

import html
from collections import Counter

from src.evidence.models import EvidenceBundle


def evidence_summary_html(bundle: EvidenceBundle) -> str:
    """Short TG-safe summary: counts/status/warnings — no quotes/HTML dumps."""
    n_src = len(bundle.sources)
    n_facts = len(bundle.facts)
    lines = [
        f"Evidence: {n_src} sources, {n_facts} facts",
        f"status={html.escape(bundle.status, quote=False)}",
    ]
    if bundle.sources:
        statuses = Counter(s.status for s in bundle.sources)
        status_bits = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
        lines.append(f"sources: {html.escape(status_bits, quote=False)}")
    err_codes = sorted({s.error_code for s in bundle.sources if s.error_code})
    if err_codes:
        lines.append(
            "errors: " + html.escape(", ".join(err_codes), quote=False)
        )
    for warning in bundle.warnings[:3]:
        short = warning if len(warning) <= 80 else f"{warning[:77]}..."
        lines.append(f"⚠️ {html.escape(short, quote=False)}")
    if len(bundle.warnings) > 3:
        lines.append(f"⚠️ +{len(bundle.warnings) - 3} more warnings")
    return "\n".join(lines)
