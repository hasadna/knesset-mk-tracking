"""Storage-independent data access contract."""

from __future__ import annotations

from typing import Any, Protocol


class Repository(Protocol):
    backend_name: str

    def list_mks(self, query: str | None = None, party: str | None = None) -> list[dict[str, Any]]: ...

    def list_issues(self) -> list[dict[str, Any]]: ...

    def get_mk(self, mk_key: str) -> dict[str, Any] | None: ...

    def get_mk_issues(self, mk_key: str) -> dict[str, Any] | None: ...

    def get_mk_posts(
        self,
        mk_key: str,
        issue: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any] | None: ...

    def get_post(self, post_key: str) -> dict[str, Any] | None: ...

    def preload_summaries(self) -> None: ...
