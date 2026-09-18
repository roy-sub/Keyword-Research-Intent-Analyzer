"""Request and response models.

The response shapes here are the API contract the frontend renders against —
notably `sources`, which preserves the exact query that produced each
suggestion and is never flattened away.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["seed", "alphabet", "question", "commercial"]
AnalysisProviderName = Literal["anthropic", "openai", "gemini"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class LoginResponse(BaseModel):
    ok: bool
    access_key: str


class LogoutResponse(BaseModel):
    ok: bool


class HealthResponse(BaseModel):
    status: str


class ServiceInfo(BaseModel):
    """Returned by GET / so the API host identifies itself in a browser."""

    service: str
    status: str
    docs: str | None = None


class StatusResponse(BaseModel):
    searches_remaining: int
    window_minutes: int
    retry_after_seconds: int
    request_delay_seconds: float
    expected_queries: int


class AnalyzeRequest(BaseModel):
    topic: str = ""


class SourceGroup(BaseModel):
    """One autocomplete query and everything it returned."""

    query: str
    type: SourceType
    keywords: list[str]


class KeywordEntry(BaseModel):
    """One unique keyword and every query/type that produced it."""

    keyword: str
    sources: list[str]
    types: list[SourceType]


class AnalyzeResponse(BaseModel):
    topic: str
    generated_at: str
    cached: bool
    duration_seconds: float
    total_keywords: int
    queries_attempted: int
    queries_succeeded: int
    failed_queries: list[str]
    searches_remaining: int
    sources: list[SourceGroup]
    keywords: list[KeywordEntry]
    analysis_markdown: str
    # Which provider actually produced the report. The chain falls back, so
    # this is not necessarily the first-choice provider.
    analysis_provider: AnalysisProviderName
    analysis_model: str


class ReportExportRequest(BaseModel):
    """What the browser posts back to have a report typeset as a PDF.

    The client already holds the finished report, so re-running the analysis
    to export it would burn quota and could return different prose. It posts
    the report back instead; every field is treated as untrusted text and is
    escaped before it reaches the PDF.
    """

    topic: str = Field(min_length=1, max_length=200)
    analysis_markdown: str = Field(min_length=1, max_length=200_000)
    generated_at: str = Field(default="", max_length=64)
    provider_label: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=120)
    total_keywords: int | None = None
    queries_succeeded: int | None = None
    queries_attempted: int | None = None
