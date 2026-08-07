"""API response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]
    backend: str


class MkResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    name: str
    party: str
    category: Literal["current_mk", "non_mk"]
    current: bool
    hasData: bool
    postCount: int
    coverage: dict[str, str]
    ratings: dict[str, int] = Field(default_factory=dict)


class IssueResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    ratingScale: dict[str, str] | None = None


class VoteItemResponse(BaseModel):
    id: str
    title: str
    vote: Literal["for", "against", "abstain", "absent"]
    date: str
    eventKind: Literal["plenum", "committee"] = "plenum"
    documentUri: str | None = None
    summary: str | None = None


class VoteSummaryResponse(BaseModel):
    forCount: int = 0
    againstCount: int = 0
    abstainCount: int = 0
    absentCount: int = 0


class OpinionResponse(BaseModel):
    id: str
    status: Literal["strong", "partial", "none"]
    rating: int | None = None
    postCount: int = 0
    stance: str
    extendedStance: str = ""
    sources: list[str]
    limitations: str
    modelVersion: str | None = None
    votes: list[VoteItemResponse] = []
    voteSummary: VoteSummaryResponse | None = None


class MkIssuesResponse(BaseModel):
    mkKey: str
    topics: list[OpinionResponse]
    sourcePosts: list[PostResponse]


class PostResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    sourceType: Literal["x"]
    publisher: str
    account: str
    text: str
    url: str


class PostListResponse(BaseModel):
    items: list[PostResponse]
    total: int
    limit: int
    offset: int
