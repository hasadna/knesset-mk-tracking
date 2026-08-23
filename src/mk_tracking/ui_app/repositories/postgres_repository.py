"""PostgreSQL-backed repository for the MK explorer.

The only live backend. It began as a straight port of the BigQuery repository,
which has since been removed (see git history for the side-by-side); the SQL
still carries the marks of that port: ``QUALIFY ROW_NUMBER() = 1`` became
``DISTINCT ON``, ``ARRAY_AGG(STRUCT(...))`` became ``jsonb_agg`` / ``array_agg``
of ``jsonb_build_object``, and ``@param`` became ``%(param)s``.
"""

from __future__ import annotations

import operator
import re
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

import psycopg
from cachetools import TTLCache, cachedmethod
from psycopg.rows import dict_row

from ...db_config import SCHEMA_PATTERN

MK_KEY_PATTERN = re.compile(r"^mk-(\d+)$")
POST_KEY_PATTERN = re.compile(r"^x:[A-Za-z0-9_.-]{1,50}:(\d{1,30})$")
TOPIC_RELEVANCE_THRESHOLD = 0.20


def _json_int(column: str, key: str) -> str:
    """`SAFE_CAST(JSON_VALUE(column, '$.key') AS INT64)` over a jsonb column.

    The jsonpath type guard reproduces SAFE_CAST: a missing key, or a value
    that is not a JSON number, yields NULL instead of raising.
    """
    return (
        f"(jsonb_path_query_first({column}, "
        f"'$.{key} ? (@.type() == \"number\")') #>> '{{}}')::numeric"
    )


class PostgresRepository:
    backend_name = "postgres"

    def __init__(
        self,
        conninfo: str,
        schema: str = "mk_tracking",
        connection_factory: Callable[[], psycopg.Connection[Any]] | None = None,
    ) -> None:
        if not SCHEMA_PATTERN.fullmatch(schema):
            raise ValueError("invalid Postgres schema")
        self.conninfo = conninfo
        self.schema = schema
        self.connection_factory = connection_factory or self._connect
        self.prefix = schema

        # Setup 30-minute TTL caches
        self._cache_lock = threading.RLock()
        self._cache_mks = TTLCache(maxsize=10, ttl=1800)
        self._cache_issues = TTLCache(maxsize=1, ttl=1800)
        self._cache_mk_issues = TTLCache(maxsize=200, ttl=1800)
        self._cache_mk_posts = TTLCache(maxsize=500, ttl=1800)
        self._cache_post = TTLCache(maxsize=1000, ttl=1800)
        self._preloaded_mk_issues: dict[str, dict[str, Any]] | None = None

    def _connect(self) -> psycopg.Connection[Any]:
        """Default factory: one short-lived connection per query."""
        return psycopg.connect(self.conninfo)

    def _query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        with self.connection_factory() as connection:
            # BigQuery timestamps are UTC; keep rendered dates and the ISO
            # strings that come out of jsonb on the same clock.
            connection.execute("SET TIME ZONE 'UTC'")
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, parameters or {})
                return cursor.fetchall()

    @staticmethod
    def _mk_id_from_key(mk_key: str) -> int | None:
        match = MK_KEY_PATTERN.fullmatch(mk_key)
        return int(match.group(1)) if match else None

    @staticmethod
    def _source_key(handle: str | None, post_id: str) -> str:
        return f"x:{handle or 'unknown'}:{post_id}"

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        """Timestamps nested inside jsonb arrive as ISO strings, not datetimes."""
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    def list_mks(
        self,
        query: str | None = None,
        party: str | None = None,
    ) -> list[dict[str, Any]]:
        all_members = self._get_all_mks()
        if not query and not party:
            return all_members

        normalized_query = query.casefold().strip() if query else None
        filtered = []
        for member in all_members:
            if party and member.get("party") != party:
                continue
            if normalized_query:
                searchable = f"{member.get('name', '')} {member.get('party', '')}".casefold()
                if normalized_query not in searchable:
                    continue
            filtered.append(member)
        return filtered

    @cachedmethod(cache=operator.attrgetter('_cache_mks'), lock=operator.attrgetter('_cache_lock'))
    def _get_all_mks(self) -> list[dict[str, Any]]:
        filters = [
            (
                "(m.is_current OR COALESCE(summary_data.issue_count, 0) > 0 "
                "OR COALESCE(post_counts.post_count, 0) > 0)"
            )
        ]
        parameters: dict[str, Any] = {}

        sql = f"""
        WITH current_party AS (
          SELECT DISTINCT ON (a.mk_id) a.mk_id, p.name_he
          FROM {self.prefix}.mk_affiliation a
          JOIN {self.prefix}.party p ON p.id=a.party_id
          WHERE a.end_date IS NULL
          ORDER BY a.mk_id, a.start_date DESC NULLS LAST, a.id
        ),
        party_sizes AS (
          SELECT p.name_he, COUNT(DISTINCT a.mk_id) AS member_count
          FROM {self.prefix}.mk_affiliation a
          JOIN {self.prefix}.party p ON p.id=a.party_id
          JOIN {self.prefix}.mk member ON member.id=a.mk_id AND member.is_current
          WHERE a.end_date IS NULL
          GROUP BY p.name_he
        ),
        summary_data AS (
          SELECT
            s.mk_id,
            COUNT(*) AS issue_count,
            jsonb_agg(jsonb_build_object('slug', i.slug, 'quality', s.quality)) AS coverage_rows
          FROM {self.prefix}.mk_issue_summary s
          JOIN {self.prefix}.issue i ON i.id=s.issue_id
          GROUP BY s.mk_id
        ),
        role_data AS (
          SELECT mk_id, array_agg(role_type::text) AS current_roles
          FROM {self.prefix}.mk_role
          WHERE end_date IS NULL
          GROUP BY mk_id
        ),
        twitter_data AS (
          SELECT DISTINCT ON (mk_id) mk_id, handle, url
          FROM {self.prefix}.mk_social_account
          WHERE platform='twitter' AND is_active
          ORDER BY mk_id, verified DESC, id
        ),
        post_counts AS (
          SELECT mk_id, COUNT(DISTINCT platform_post_id) AS post_count
          FROM {self.prefix}.social_post
          WHERE platform='twitter' AND NOT is_deleted
          GROUP BY mk_id
        )
        SELECT
          m.knesset_member_id,
          m.slug,
          m.full_name_he,
          m.photo_url,
          m.bio_he,
          m.is_current,
          COALESCE(p.name_he, 'ללא מפלגה') AS party_name,
          COALESCE(ps.member_count, 0) AS party_size,
          COALESCE(summary_data.coverage_rows, '[]'::jsonb) AS coverage_rows,
          COALESCE(role_data.current_roles, ARRAY[]::text[]) AS current_roles,
          twitter_data.handle AS twitter_handle,
          twitter_data.url AS twitter_url,
          COALESCE(post_counts.post_count, 0) AS post_count
        FROM {self.prefix}.mk m
        LEFT JOIN current_party p ON p.mk_id=m.id
        LEFT JOIN party_sizes ps ON ps.name_he=p.name_he
        LEFT JOIN summary_data ON summary_data.mk_id=m.id
        LEFT JOIN role_data ON role_data.mk_id=m.id
        LEFT JOIN twitter_data ON twitter_data.mk_id=m.id
        LEFT JOIN post_counts ON post_counts.mk_id=m.id
        WHERE {" AND ".join(filters)}
        ORDER BY m.is_current DESC, party_size DESC, m.full_name_he
        """

        members = []
        for row in self._query(sql, parameters):
            coverage = {
                item["slug"]: item["quality"]
                for item in row["coverage_rows"]
            }
            roles = set(row["current_roles"])
            if "coalition" in roles:
                bloc = "קואליציה"
            elif "opposition" in roles:
                bloc = "אופוזיציה"
            else:
                party_name = row["party_name"] or ""
                if any(p in party_name for p in ["ליכוד", "ש\"ס", "יהדות התורה", "הציונות הדתית", "עוצמה יהודית", "נעם"]):
                    bloc = "קואליציה"
                elif any(p in party_name for p in ["יש עתיד", "מחנה ממלכתי", "כחול לבן", "ישראל ביתנו", "רע\"ם", "רע\"מ", "חד\"ש", "תע\"ל", "עבודה", "דמוקרטים", "מרצ", "תקווה חדשה"]):
                    bloc = "אופוזיציה"
                else:
                    bloc = "אופוזיציה"
            members.append(
                {
                    "key": f"mk-{row['knesset_member_id']}",
                    "name": row["full_name_he"],
                    "wikiTitle": row["full_name_he"],
                    "party": row["party_name"],
                    "category": "current_mk" if row["is_current"] else "non_mk",
                    "seats": row["party_size"],
                    "bloc": bloc,
                    "current": row["is_current"],
                    "hasData": bool(coverage),
                    "postCount": row["post_count"],
                    "coverage": coverage,
                    "imageUrl": row["photo_url"] or "",
                    "account": row["twitter_handle"] or "",
                    "bio": row["bio_he"] or "",
                }
            )
        return members

    @cachedmethod(cache=operator.attrgetter('_cache_issues'), lock=operator.attrgetter('_cache_lock'))
    def list_issues(self) -> list[dict[str, Any]]:
        import json

        sql = f"""
        SELECT slug, name, description, prompt_for_social_post_similarity, rating_scale, sort_order
        FROM {self.prefix}.issue
        ORDER BY sort_order, slug
        """
        results = []
        for row in self._query(sql):
            raw_scale = row["rating_scale"]
            if isinstance(raw_scale, str) and raw_scale.strip():
                try:
                    rating_scale = json.loads(raw_scale)
                except (json.JSONDecodeError, TypeError):
                    rating_scale = None
            elif isinstance(raw_scale, dict):
                rating_scale = raw_scale
            else:
                rating_scale = None

            results.append(
                {
                    "id": row["slug"],
                    "title": row["name"],
                    "description": row["description"] or "",
                    "promptForSocialPostSimilarity": row["prompt_for_social_post_similarity"] or "",
                    "ratingScale": rating_scale,
                    "group": "",
                    "tags": [],
                    "contrast": "",
                }
            )
        return results

    def get_mk(self, mk_key: str) -> dict[str, Any] | None:
        matches = self.list_mks()
        return next((member for member in matches if member["key"] == mk_key), None)

    def preload_summaries(self) -> None:
        sql = f"""
        WITH topic_relevant_posts AS (
          SELECT topic_post.mk_id, pi.issue_id, pi.post_id
          FROM {self.prefix}.post_issue pi
          JOIN {self.prefix}.social_post topic_post ON topic_post.id=pi.post_id
          WHERE pi.confidence >= {TOPIC_RELEVANCE_THRESHOLD}

          UNION

          SELECT summary.mk_id, summary.issue_id, link.post_id
          FROM {self.prefix}.mk_issue_summary summary
          JOIN {self.prefix}.mk_issue_summary_supporting_post link
            ON link.summary_id=summary.id
        ),
        topic_post_counts AS (
          SELECT mk_id, issue_id, COUNT(DISTINCT post_id) AS post_count
          FROM topic_relevant_posts
          GROUP BY mk_id, issue_id
        ),
        topic_votes_dedup AS (
          SELECT DISTINCT ON (v.mk_id, vei.issue_id, COALESCE(b.title_he, ve.title_he))
            v.mk_id,
            vei.issue_id,
            v.id AS vote_id,
            COALESCE(b.title_he, ve.title_he) AS title,
            v.vote,
            ve.occurred_at AS voted_at,
            ve.event_kind,
            COALESCE(ve.draft_document_uri, b.enacted_law_document_uri) AS document_uri,
            b.summary AS bill_summary
          FROM {self.prefix}.mk_vote v
          JOIN {self.prefix}.vote_event ve ON ve.id = v.vote_event_id
          JOIN {self.prefix}.v_vote_event_issues vei ON vei.vote_event_id = ve.id
          LEFT JOIN {self.prefix}.bill b ON b.id = ve.bill_id
          ORDER BY
            v.mk_id, vei.issue_id, COALESCE(b.title_he, ve.title_he),
            ve.occurred_at DESC NULLS LAST
        ),
        topic_votes_cte AS (
          SELECT
            mk_id,
            issue_id,
            (array_agg(
              jsonb_build_object(
                'vote_id', vote_id,
                'title', title,
                'vote', vote,
                'voted_at', voted_at,
                'event_kind', event_kind,
                'document_uri', document_uri,
                'bill_summary', bill_summary
              )
              ORDER BY voted_at DESC NULLS LAST
            ))[1:10] AS votes_list
          FROM topic_votes_dedup
          GROUP BY mk_id, issue_id
        )
        SELECT
          m.knesset_member_id,
          m.full_name_he AS publisher,
          i.slug,
          s.summary_he,
          s.quality,
          s.rating,
          COALESCE(topic_counts.post_count, 0) AS topic_post_count,
          s.limitations,
          s.model_version,
          COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
              'platform_post_id', post.platform_post_id,
              'handle', account.handle,
              'posted_at', post.posted_at,
              'text', post.text,
              'url', post.url,
              'engagement', post.engagement
            ))
            FROM {self.prefix}.mk_issue_summary_supporting_post link
            JOIN {self.prefix}.social_post post ON post.id=link.post_id
            LEFT JOIN {self.prefix}.mk_social_account account ON account.id=post.account_id
            WHERE link.summary_id=s.id
              AND post.platform='twitter'
          ), '[]'::jsonb) AS supporting_posts,
          COALESCE(tv.votes_list, ARRAY[]::jsonb[]) AS topic_votes
        FROM {self.prefix}.mk m
        CROSS JOIN {self.prefix}.issue i
        LEFT JOIN {self.prefix}.mk_issue_summary s
          ON s.mk_id=m.id AND s.issue_id=i.id
        LEFT JOIN topic_post_counts topic_counts
          ON topic_counts.mk_id=m.id AND topic_counts.issue_id=i.id
        LEFT JOIN topic_votes_cte tv
          ON tv.mk_id=m.id AND tv.issue_id=i.id
        WHERE m.knesset_member_id IS NOT NULL
        ORDER BY m.knesset_member_id, i.sort_order, i.slug
        """
        rows = self._query(sql)
        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            km_id = row["knesset_member_id"]
            if km_id not in grouped:
                grouped[km_id] = []
            grouped[km_id].append(row)

        preloaded: dict[str, dict[str, Any]] = {}
        for km_id, mk_rows in grouped.items():
            mk_key = f"mk-{km_id}"
            topics = []
            source_posts: dict[str, dict[str, Any]] = {}
            for row in mk_rows:
                sources = [
                    self._source_key(item["handle"], item["platform_post_id"])
                    for item in row["supporting_posts"]
                ]
                for item in row["supporting_posts"]:
                    post_id = str(item["platform_post_id"])
                    handle = item["handle"] or "unknown"
                    key = self._source_key(handle, post_id)
                    posted_at = self._as_datetime(item["posted_at"])
                    source_posts[key] = {
                        "key": key,
                        "sourceType": "x",
                        "publisher": row["publisher"] or "",
                        "account": handle,
                        "date": posted_at.date().isoformat() if posted_at else "",
                        "createdAt": posted_at.isoformat() if posted_at else "",
                        "text": item["text"] or "",
                        "url": item["url"] or f"https://x.com/{handle}/status/{post_id}",
                        "tweetId": post_id,
                        "metrics": item["engagement"] or {},
                        "referencedTweets": [],
                        "label": "ציוץ",
                        "isSummarySource": True,
                    }
                votes = [
                    {
                        "id": str(v["vote_id"]),
                        "title": v["title"] or "הצבעת כנסת",
                        "vote": v["vote"],
                        "date": voted_at.date().isoformat() if (voted_at := self._as_datetime(v.get("voted_at"))) else "",
                        "eventKind": v.get("event_kind") or "plenum",
                        "documentUri": v.get("document_uri") or None,
                        "summary": v.get("bill_summary") or None,
                    }
                    for v in (row.get("topic_votes") or [])
                ]
                vote_summary = {
                    "forCount": sum(1 for v in votes if v["vote"] == "for"),
                    "againstCount": sum(1 for v in votes if v["vote"] == "against"),
                    "abstainCount": sum(1 for v in votes if v["vote"] == "abstain"),
                    "absentCount": sum(1 for v in votes if v["vote"] == "absent"),
                }
                topics.append(
                    {
                        "id": row["slug"],
                        "status": row["quality"] or "none",
                        "rating": row["rating"],
                        "postCount": row["topic_post_count"] or 0,
                        "stance": row["summary_he"] or "הנושא אינו מכוסה במאגר.",
                        "extendedStance": "",
                        "sources": sources,
                        "limitations": row["limitations"] or "",
                        "modelVersion": row["model_version"],
                        "votes": votes,
                        "voteSummary": vote_summary,
                    }
                )
            preloaded[mk_key] = {
                "mkKey": mk_key,
                "topics": topics,
                "sourcePosts": list(source_posts.values()),
            }

        with self._cache_lock:
            self._preloaded_mk_issues = preloaded

    def get_mk_issues(self, mk_key: str) -> dict[str, Any] | None:
        with self._cache_lock:
            if self._preloaded_mk_issues is not None:
                return self._preloaded_mk_issues.get(mk_key)

        return self._fetch_single_mk_issues(mk_key)

    @cachedmethod(cache=operator.attrgetter('_cache_mk_issues'), lock=operator.attrgetter('_cache_lock'))
    def _fetch_single_mk_issues(self, mk_key: str) -> dict[str, Any] | None:
        knesset_member_id = self._mk_id_from_key(mk_key)
        if knesset_member_id is None:
            return None
        sql = f"""
        WITH target_mk AS (
          SELECT id, full_name_he FROM {self.prefix}.mk WHERE knesset_member_id=%(mk_id)s
        ),
        topic_relevant_posts AS (
          SELECT topic_post.mk_id, pi.issue_id, pi.post_id
          FROM {self.prefix}.post_issue pi
          JOIN {self.prefix}.social_post topic_post ON topic_post.id=pi.post_id
          JOIN target_mk ON target_mk.id=topic_post.mk_id
          WHERE pi.confidence >= {TOPIC_RELEVANCE_THRESHOLD}

          UNION

          SELECT summary.mk_id, summary.issue_id, link.post_id
          FROM {self.prefix}.mk_issue_summary summary
          JOIN target_mk ON target_mk.id=summary.mk_id
          JOIN {self.prefix}.mk_issue_summary_supporting_post link
            ON link.summary_id=summary.id
        ),
        topic_post_counts AS (
          SELECT mk_id, issue_id, COUNT(DISTINCT post_id) AS post_count
          FROM topic_relevant_posts
          GROUP BY mk_id, issue_id
        ),
        topic_votes_dedup AS (
          SELECT DISTINCT ON (v.mk_id, vei.issue_id, COALESCE(b.title_he, ve.title_he))
            v.mk_id,
            vei.issue_id,
            v.id AS vote_id,
            COALESCE(b.title_he, ve.title_he) AS title,
            v.vote,
            ve.occurred_at AS voted_at,
            ve.event_kind,
            COALESCE(ve.draft_document_uri, b.enacted_law_document_uri) AS document_uri,
            b.summary AS bill_summary
          FROM {self.prefix}.mk_vote v
          JOIN {self.prefix}.vote_event ve ON ve.id = v.vote_event_id
          JOIN {self.prefix}.v_vote_event_issues vei ON vei.vote_event_id = ve.id
          LEFT JOIN {self.prefix}.bill b ON b.id = ve.bill_id
          JOIN target_mk ON target_mk.id = v.mk_id
          ORDER BY
            v.mk_id, vei.issue_id, COALESCE(b.title_he, ve.title_he),
            ve.occurred_at DESC NULLS LAST
        ),
        topic_votes_cte AS (
          SELECT
            mk_id,
            issue_id,
            (array_agg(
              jsonb_build_object(
                'vote_id', vote_id,
                'title', title,
                'vote', vote,
                'voted_at', voted_at,
                'event_kind', event_kind,
                'document_uri', document_uri,
                'bill_summary', bill_summary
              )
              ORDER BY voted_at DESC NULLS LAST
            ))[1:10] AS votes_list
          FROM topic_votes_dedup
          GROUP BY mk_id, issue_id
        )
        SELECT
          m.id AS mk_id,
          m.full_name_he AS publisher,
          i.slug,
          s.summary_he,
          s.quality,
          s.rating,
          COALESCE(topic_counts.post_count, 0) AS topic_post_count,
          s.limitations,
          s.model_version,
          COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
              'platform_post_id', post.platform_post_id,
              'handle', account.handle,
              'posted_at', post.posted_at,
              'text', post.text,
              'url', post.url,
              'engagement', post.engagement
            ))
            FROM {self.prefix}.mk_issue_summary_supporting_post link
            JOIN {self.prefix}.social_post post ON post.id=link.post_id
            LEFT JOIN {self.prefix}.mk_social_account account ON account.id=post.account_id
            WHERE link.summary_id=s.id
              AND post.platform='twitter'
          ), '[]'::jsonb) AS supporting_posts,
          COALESCE(tv.votes_list, ARRAY[]::jsonb[]) AS topic_votes
        FROM target_mk m
        CROSS JOIN {self.prefix}.issue i
        LEFT JOIN {self.prefix}.mk_issue_summary s
          ON s.mk_id=m.id AND s.issue_id=i.id
        LEFT JOIN topic_post_counts topic_counts
          ON topic_counts.mk_id=m.id AND topic_counts.issue_id=i.id
        LEFT JOIN topic_votes_cte tv
          ON tv.mk_id=m.id AND tv.issue_id=i.id
        ORDER BY i.sort_order, i.slug
        """
        rows = self._query(sql, {"mk_id": knesset_member_id})
        if not rows:
            return None
        topics = []
        source_posts: dict[str, dict[str, Any]] = {}
        for row in rows:
            sources = [
                self._source_key(item["handle"], item["platform_post_id"])
                for item in row["supporting_posts"]
            ]
            for item in row["supporting_posts"]:
                post_id = str(item["platform_post_id"])
                handle = item["handle"] or "unknown"
                key = self._source_key(handle, post_id)
                posted_at = self._as_datetime(item["posted_at"])
                source_posts[key] = {
                    "key": key,
                    "sourceType": "x",
                    "publisher": row["publisher"] or "",
                    "account": handle,
                    "date": posted_at.date().isoformat() if posted_at else "",
                    "createdAt": posted_at.isoformat() if posted_at else "",
                    "text": item["text"] or "",
                    "url": item["url"] or f"https://x.com/{handle}/status/{post_id}",
                    "tweetId": post_id,
                    "metrics": item["engagement"] or {},
                    "referencedTweets": [],
                    "label": "ציוץ",
                    "isSummarySource": True,
                }
            votes = [
                {
                    "id": str(v["vote_id"]),
                    "title": v["title"] or "הצבעת כנסת",
                    "vote": v["vote"],
                    "date": voted_at.date().isoformat() if (voted_at := self._as_datetime(v.get("voted_at"))) else "",
                    "eventKind": v.get("event_kind") or "plenum",
                    "documentUri": v.get("document_uri") or None,
                    "summary": v.get("bill_summary") or None,
                }
                for v in (row.get("topic_votes") or [])
            ]
            vote_summary = {
                "forCount": sum(1 for v in votes if v["vote"] == "for"),
                "againstCount": sum(1 for v in votes if v["vote"] == "against"),
                "abstainCount": sum(1 for v in votes if v["vote"] == "abstain"),
                "absentCount": sum(1 for v in votes if v["vote"] == "absent"),
            }
            topics.append(
                {
                    "id": row["slug"],
                    "status": row["quality"] or "none",
                    "rating": row["rating"],
                    "postCount": row["topic_post_count"] or 0,
                    "stance": row["summary_he"] or "הנושא אינו מכוסה במאגר.",
                    "extendedStance": "",
                    "sources": sources,
                    "limitations": row["limitations"] or "",
                    "modelVersion": row["model_version"],
                    "votes": votes,
                    "voteSummary": vote_summary,
                }
            )
        return {
            "mkKey": mk_key,
            "topics": topics,
            "sourcePosts": list(source_posts.values()),
        }

    @cachedmethod(cache=operator.attrgetter('_cache_mk_posts'), lock=operator.attrgetter('_cache_lock'))
    def get_mk_posts(
        self,
        mk_key: str,
        issue: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any] | None:
        if offset > 1000:
            raise ValueError("offset exceeds maximum allowed threshold of 1000")
        knesset_member_id = self._mk_id_from_key(mk_key)
        if knesset_member_id is None:
            return None
        parameters: dict[str, Any] = {
            "mk_id": knesset_member_id,
            "limit": limit,
            "offset": offset,
        }
        issue_filter = ""
        if issue:
            issue_filter = "AND issue.slug=%(issue)s"
            parameters["issue"] = issue

        sql = f"""
        WITH ranked_posts AS (
          SELECT
            post.platform_post_id,
            post.posted_at,
            post.text,
            post.url,
            post.engagement,
            m.full_name_he AS publisher,
            account.handle,
            tag.confidence::float8 AS tag_confidence,
            tag.is_concrete_promise,
            issue.slug AS issue_slug,
            EXISTS (
              SELECT 1
              FROM {self.prefix}.mk_issue_summary summary
              JOIN {self.prefix}.mk_issue_summary_supporting_post link
                ON link.summary_id=summary.id
              JOIN {self.prefix}.social_post linked_post
                ON linked_post.id=link.post_id
              WHERE summary.mk_id=post.mk_id
                AND summary.issue_id=tag.issue_id
                AND linked_post.platform=post.platform
                AND linked_post.platform_post_id=post.platform_post_id
            ) AS is_summary_source,
            (COALESCE({_json_int("post.engagement", "like_count")}, 0)
              + 2 * COALESCE({_json_int("post.engagement", "retweet_count")}, 0)
              + COALESCE({_json_int("post.engagement", "reply_count")}, 0)
            )::bigint AS engagement_score,
            ROW_NUMBER() OVER (
              PARTITION BY post.platform, post.platform_post_id
              ORDER BY post.fetched_at DESC NULLS LAST, post.created_at DESC
            ) AS duplicate_rank
          FROM {self.prefix}.social_post post
          JOIN {self.prefix}.mk m ON m.id=post.mk_id
          LEFT JOIN {self.prefix}.mk_social_account account ON account.id=post.account_id
          LEFT JOIN {self.prefix}.post_issue tag ON tag.post_id=post.id
          LEFT JOIN {self.prefix}.issue issue ON issue.id=tag.issue_id
          WHERE m.knesset_member_id=%(mk_id)s
            AND post.platform='twitter'
            AND NOT post.is_deleted
            {issue_filter}
        ),
        deduplicated AS (
          SELECT
            platform_post_id, posted_at, text, url, engagement, publisher, handle,
            tag_confidence, is_concrete_promise, issue_slug, is_summary_source,
            engagement_score
          FROM ranked_posts
          WHERE duplicate_rank=1
        )
        SELECT *, COUNT(*) OVER() AS total_count
        FROM deduplicated
        ORDER BY
          (CASE WHEN %(has_issue)s::boolean THEN is_summary_source ELSE FALSE END) DESC NULLS LAST,
          (CASE WHEN %(has_issue)s::boolean THEN tag_confidence ELSE NULL END) DESC NULLS LAST,
          (CASE WHEN %(has_issue)s::boolean THEN is_concrete_promise ELSE FALSE END) DESC NULLS LAST,
          engagement_score DESC,
          posted_at DESC NULLS LAST
        LIMIT %(limit)s OFFSET %(offset)s
        """
        parameters["has_issue"] = bool(issue)
        rows = self._query(sql, parameters)
        items = [self._post_from_row(row) for row in rows]
        total = rows[0]["total_count"] if rows else 0
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def _post_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        post_id = str(row["platform_post_id"])
        handle = row["handle"] or "unknown"
        posted_at = row["posted_at"]
        return {
            "key": self._source_key(handle, post_id),
            "sourceType": "x",
            "publisher": row["publisher"] or "",
            "account": handle,
            "date": posted_at.date().isoformat() if posted_at else "",
            "createdAt": posted_at.isoformat() if posted_at else "",
            "text": row["text"] or "",
            "url": row["url"] or f"https://x.com/{handle}/status/{post_id}",
            "tweetId": post_id,
            "metrics": row["engagement"] or {},
            "referencedTweets": [],
            "label": "ציוץ",
            "isSummarySource": bool(row.get("is_summary_source", False)),
            "tagConfidence": row.get("tag_confidence"),
            "isConcretePromise": bool(row.get("is_concrete_promise", False)),
            "engagementScore": row.get("engagement_score", 0),
        }

    @cachedmethod(cache=operator.attrgetter('_cache_post'), lock=operator.attrgetter('_cache_lock'))
    def get_post(self, post_key: str) -> dict[str, Any] | None:
        if not POST_KEY_PATTERN.fullmatch(post_key):
            return None
        post_id = post_key.rsplit(":", 1)[-1]
        sql = f"""
        SELECT
          post.platform_post_id, post.posted_at, post.text, post.url, post.engagement,
          m.full_name_he AS publisher, account.handle,
          FALSE AS is_summary_source,
          NULL::float8 AS tag_confidence,
          FALSE AS is_concrete_promise,
          0 AS engagement_score
        FROM {self.prefix}.social_post post
        JOIN {self.prefix}.mk m ON m.id=post.mk_id
        LEFT JOIN {self.prefix}.mk_social_account account ON account.id=post.account_id
        WHERE post.platform='twitter' AND post.platform_post_id=%(post_id)s
        ORDER BY post.fetched_at DESC NULLS LAST, post.created_at DESC
        LIMIT 1
        """
        rows = self._query(sql, {"post_id": post_id})
        return self._post_from_row(rows[0]) if rows else None
