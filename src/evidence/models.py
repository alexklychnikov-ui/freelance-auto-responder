from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceSource(BaseModel):
    id: str
    kind: Literal["url", "attachment"]
    role: Literal[
        "reference",
        "data_source",
        "documentation",
        "repository",
        "unknown",
    ] = "unknown"
    input_ref: str
    final_url: str | None = None
    title: str | None = None
    fetch_method: Literal["http", "browser", "attachment", "offline"] = "http"
    status: Literal["verified", "partial", "unavailable", "rejected"] = "unavailable"
    http_status: int | None = None
    content_type: str | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = None
    error_code: str | None = None


class EvidenceFact(BaseModel):
    id: str
    source_id: str
    claim: str
    quote: str = ""
    anchors: list[str] = Field(default_factory=list)
    relevance: float = 0.0
    verification: Literal["exact_quote", "structured_value"] = "exact_quote"
    eligible_for_response: bool = False


class EvidenceInsight(BaseModel):
    fact_ids: list[str] = Field(default_factory=list)
    finding: str
    implementation_consequence: str = ""
    kind: Literal["scope", "architecture", "risk", "difference"] = "scope"


class EvidenceBundle(BaseModel):
    schema_version: str = "1"
    status: Literal["not_required", "complete", "partial", "failed"] = "not_required"
    required: bool = False
    project_hash: str = ""
    sources: list[EvidenceSource] = Field(default_factory=list)
    facts: list[EvidenceFact] = Field(default_factory=list)
    insights: list[EvidenceInsight] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    collected_at: datetime | None = None
