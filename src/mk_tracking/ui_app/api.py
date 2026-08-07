"""FastAPI routes for the MK explorer."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .models import (
    HealthResponse,
    IssueResponse,
    MkIssuesResponse,
    MkResponse,
    PostListResponse,
    PostResponse,
)
from .repositories.base import Repository

router = APIRouter(prefix="/api")


def get_repository(request: Request) -> Repository:
    return request.app.state.repository


RepositoryDependency = Annotated[Repository, Depends(get_repository)]


@router.get("/health", response_model=HealthResponse)
def health(repository: RepositoryDependency) -> HealthResponse:
    return HealthResponse(status="ok", backend=repository.backend_name)


@router.get("/mks", response_model=list[MkResponse])
def list_mks(
    repository: RepositoryDependency,
    q: Annotated[str | None, Query(max_length=100)] = None,
    party: Annotated[str | None, Query(max_length=100)] = None,
) -> list[dict]:
    return repository.list_mks(q, party)


@router.get("/issues", response_model=list[IssueResponse])
def list_issues(repository: RepositoryDependency) -> list[dict]:
    return repository.list_issues()


@router.get("/mks/{mk_key}/issues", response_model=MkIssuesResponse)
def get_mk_issues(mk_key: str, repository: RepositoryDependency) -> dict:
    result = repository.get_mk_issues(mk_key)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown MK")
    return result


@router.get("/mks/{mk_key}/posts", response_model=PostListResponse)
def get_mk_posts(
    mk_key: str,
    repository: RepositoryDependency,
    issue: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    try:
        result = repository.get_mk_posts(mk_key, issue, limit, offset)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown MK")
    return result


@router.get("/posts/{post_key}", response_model=PostResponse)
def get_post(post_key: str, repository: RepositoryDependency) -> dict:
    result = repository.get_post(post_key)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown post")
    return result
