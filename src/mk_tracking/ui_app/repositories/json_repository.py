"""Read-only repository backed by the current JSON snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonRepository:
    backend_name = "json"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.roster = self._load("assets/roster.json")
        self.issues = self._load("assets/topics.json")
        self.party_info = self._load("assets/party-info.json")
        self.account_stats = self._load("assets/account-stats.json")
        self.analysis = self._load("data/analysis.json")
        self.posts = self._load("data/tweets.normalized.json")
        self._validate()

        self.mks_by_key = {member["key"]: member for member in self.roster}
        self.analysis_by_issue = {issue["id"]: issue for issue in self.analysis}
        self.posts_by_key = {post["key"]: post for post in self.posts}

    def _load(self, relative_path: str) -> Any:
        path = self.root / relative_path
        return json.loads(path.read_text(encoding="utf-8"))

    def _validate(self) -> None:
        if not isinstance(self.roster, list) or not self.roster:
            raise ValueError("assets/roster.json must be a non-empty array")
        keys = [member.get("key") for member in self.roster]
        if any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("every roster member requires a stable key")
        if len(keys) != len(set(keys)):
            raise ValueError("roster keys must be unique")
        if not isinstance(self.issues, list) or not self.issues:
            raise ValueError("assets/topics.json must be a non-empty array")
        if not isinstance(self.analysis, list):
            raise ValueError("data/analysis.json must be an array")
        if not isinstance(self.posts, list) or len(self.posts) != 850:
            raise ValueError("data/tweets.normalized.json must contain exactly 850 tweets")

        post_keys: set[str] = set()
        for post in self.posts:
            key = post.get("key")
            if not isinstance(key, str) or not key.startswith("x:"):
                raise ValueError(f"invalid post key: {key!r}")
            if post.get("sourceType") != "x":
                raise ValueError(f"non-X post source: {key}")
            if key in post_keys:
                raise ValueError(f"duplicate post key: {key}")
            post_keys.add(key)

        for issue in self.analysis:
            for politician, opinion in issue.get("members", {}).items():
                status = opinion.get("status")
                sources = opinion.get("sources")
                label = f"{issue.get('id')}/{politician}"
                if status not in {"strong", "partial", "none"}:
                    raise ValueError(f"{label} has invalid status")
                if not isinstance(sources, list):
                    raise ValueError(f"{label} sources must be an array")
                if status in {"strong", "partial"} and not sources:
                    raise ValueError(f"{label} requires a source")
                if status == "none" and sources:
                    raise ValueError(f"{label} with status none cannot have sources")
                for source in sources:
                    if not isinstance(source, str) or not source.startswith("x:"):
                        raise ValueError(f"{label} contains a non-X source")
                    if source not in post_keys:
                        raise ValueError(f"{label} references unknown source {source}")

    def _enriched_member(self, member: dict[str, Any]) -> dict[str, Any]:
        result = dict(member)
        result["category"] = "current_mk" if member.get("current") else "non_mk"
        result["ratings"] = {
            issue["id"]: opinion["rating"]
            for issue in self.analysis
            if (opinion := issue.get("members", {}).get(member["name"]))
            and opinion.get("rating") is not None
        }
        account = member.get("account")
        stats = self.account_stats.get(account) if account else None
        if stats:
            result["postCount"] = stats["tweets"]
        return result

    def list_mks(
        self,
        query: str | None = None,
        party: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_query = query.casefold().strip() if query else None
        results = []
        for member in self.roster:
            if party and member.get("party") != party:
                continue
            searchable = f"{member.get('name', '')} {member.get('party', '')}".casefold()
            if normalized_query and normalized_query not in searchable:
                continue
            results.append(self._enriched_member(member))
        return results

    def list_issues(self) -> list[dict[str, Any]]:
        return self.issues

    def get_mk(self, mk_key: str) -> dict[str, Any] | None:
        member = self.mks_by_key.get(mk_key)
        return self._enriched_member(member) if member else None

    def get_mk_issues(self, mk_key: str) -> dict[str, Any] | None:
        member = self.mks_by_key.get(mk_key)
        if not member:
            return None
        politician = member["name"]
        topics = []
        for issue in self.analysis:
            opinion = issue.get("members", {}).get(politician)
            if opinion is None:
                opinion = {
                    "status": "none",
                    "stance": "הנושא אינו מכוסה בציוצים שבמאגר.",
                    "extendedStance": "",
                    "sources": [],
                    "limitations": "",
                }
            else:
                opinion = dict(opinion)
                opinion.setdefault("extendedStance", "")
                opinion.setdefault("limitations", opinion.pop("limit", ""))
            opinion.setdefault("postCount", len(opinion.get("sources", [])))
            opinion.setdefault("votes", [])
            opinion.setdefault("voteSummary", {"forCount": 0, "againstCount": 0, "abstainCount": 0, "absentCount": 0})
            topics.append({"id": issue["id"], **opinion})
        source_keys = {
            source
            for topic in topics
            for source in topic.get("sources", [])
        }
        source_posts = [
            self.posts_by_key[key]
            for key in source_keys
            if key in self.posts_by_key
        ]
        return {"mkKey": mk_key, "topics": topics, "sourcePosts": source_posts}

    def get_mk_posts(
        self,
        mk_key: str,
        issue: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any] | None:
        member = self.mks_by_key.get(mk_key)
        if not member:
            return None

        if offset > 1000:
            raise ValueError("offset exceeds maximum allowed threshold of 1000")

        if issue:
            analysis_issue = self.analysis_by_issue.get(issue)
            if not analysis_issue:
                raise ValueError(f"unknown issue: {issue}")
            opinion = analysis_issue.get("members", {}).get(member["name"], {})
            source_keys = opinion.get("sources", [])
            matches = [self.posts_by_key[key] for key in source_keys if key in self.posts_by_key]
        else:
            account = member.get("account")
            matches = [post for post in self.posts if account and post.get("account") == account]

        total = len(matches)
        return {
            "items": matches[offset : offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_post(self, post_key: str) -> dict[str, Any] | None:
        return self.posts_by_key.get(post_key)

    def preload_summaries(self) -> None:
        pass

