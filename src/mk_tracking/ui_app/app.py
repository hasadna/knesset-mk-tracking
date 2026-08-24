"""FastAPI application construction and static frontend serving."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from ..db_config import ConfigError, resolve_dsn, resolve_schema
from .api import router
from .repositories import JsonRepository, PostgresRepository, Repository

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_ROOT = PROJECT_ROOT / "ui"
ROOT = UI_ROOT


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https: blob:; "
            "connect-src 'self' https://he.wikipedia.org https://upload.wikimedia.org https://commons.wikimedia.org;"
        )
        response.headers["Server"] = "MK-Tracking"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 120, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/"):
            client_ip = request.client.host if request.client else "127.0.0.1"
            now = time.time()
            timestamps = self.requests[client_ip]
            # Prune expired timestamps
            self.requests[client_ip] = [
                t for t in timestamps if now - t < self.window_seconds
            ]
            if len(self.requests[client_ip]) >= self.max_requests:
                return Response(
                    content='{"detail":"Rate limit exceeded. Too many requests."}',
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": str(self.window_seconds)},
                )
            self.requests[client_ip].append(now)

        return await call_next(request)


def configured_repository() -> Repository:
    """Pick the serving backend. PostgreSQL is the only live one.

    `json` remains for offline UI work against a checked-in fixture; it reads
    ui/data/analysis.json and talks to no database. There is deliberately no
    BigQuery option and no fallback to one — Postgres is the serving layer.
    """
    backend = os.getenv("MK_WORK_DATA_BACKEND", "postgres").lower()
    if backend == "json":
        return JsonRepository(UI_ROOT)
    if backend != "postgres":
        raise ValueError(
            f"unsupported MK_WORK_DATA_BACKEND: {backend!r} (expected 'postgres' or 'json')"
        )
    try:
        conninfo = resolve_dsn()
        schema = resolve_schema()
    except ConfigError as error:
        # Same resolution the db/ scripts use, so a database configured for the
        # loader also serves — including the discrete PG* variables.
        raise RuntimeError(str(error)) from None
    return PostgresRepository(conninfo, schema=schema)


def create_app(repository: Repository | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repo = repository or configured_repository()
        app.state.repository = repo
        try:
            repo.list_issues()
            repo.list_mks()
            repo.preload_summaries()
        except Exception as error:
            print(f"Cache pre-warm warning: {error}")
        yield

    app = FastAPI(title="MK Opinions Explorer API", lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware, max_requests=200, window_seconds=60)
    app.include_router(router)
    dist_dir = UI_ROOT / "dist"
    entrypoint = dist_dir / "index.html"
    if not entrypoint.is_file():
        raise RuntimeError(
            "The compiled UI is missing. Run `npm ci && npm run build` in ui/ "
            "before starting mkwork."
        )
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="frontend")
    return app


app = create_app()
