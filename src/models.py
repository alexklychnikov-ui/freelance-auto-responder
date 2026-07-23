from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProjectPreview(BaseModel):
    platform: str
    source_key: str
    project_id: str
    url: str
    title: str
    budget_text: str | None = None
    published_at: datetime | None = None
    responses_count: int | None = None


class ProjectFull(BaseModel):
    platform: str
    source_key: str
    project_id: str
    url: str
    title: str
    full_description: str
    desired_budget: str | None = None
    max_budget: str | None = None
    offers_count: int | None = None
    buyer: str | None = None
    buyer_hire_rate: str | None = None
    time_left: str | None = None
    tags: list[str] = Field(default_factory=list)


class GptScoreResult(BaseModel):
    score: int = Field(ge=0, le=10)
    fit: bool
    reason: str
    matched_skills: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    suggested_project_type: str
    competition_level: Literal["low", "medium", "high"]
    recommendation: Literal["откликаться", "пропустить", "наблюдать"]


class OfferTerms(BaseModel):
    price_rub: int = Field(ge=500)
    delivery_days: int = Field(ge=1, le=60)
    plan_summary: str = ""


class SubmitResult(BaseModel):
    success: bool
    project_id: str
    message: str | None = None


class ReplyEvent(BaseModel):
    platform: str
    source_key: str
    project_id: str
    message: str
    received_at: datetime


class PendingOffer(BaseModel):
    platform: str
    source_key: str
    project_id: str
    url: str
    title: str
    project: ProjectFull
    score: GptScoreResult
    acceptance_tier: Literal["standard", "quick_win", "experience_win"] | None = None
    created_at: datetime
    status: Literal[
        "pending", "approved", "rejected", "expired", "submitted", "prepared"
    ] = "pending"
    response_text: str | None = None
    approved_at: datetime | None = None
    telegram_message_id: int | None = None
    draft_message_id: int | None = None
