from __future__ import annotations

import re
from typing import Any

from google.cloud import bigquery

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class BigQuerySource:
    """Read the current MK, issue, account, and post schema from BigQuery."""

    def __init__(self, project: str, dataset: str) -> None:
        if not _IDENTIFIER.fullmatch(dataset):
            raise ValueError("dataset must be a BigQuery identifier")
        self.project = project
        self.dataset = dataset
        self.client = bigquery.Client(project=project)
        self._issues_cache: list[Any] | None = None

    @property
    def prefix(self) -> str:
        return f"`{self.project}.{self.dataset}"

    def _query(self, sql: str, parameters: list[bigquery.ScalarQueryParameter] | None = None):
        config = bigquery.QueryJobConfig(query_parameters=parameters or [])
        return list(self.client.query(sql, job_config=config).result())

    def load_inputs(
        self, politician: str, top_posts_per_issue: int = 10, min_confidence: float = 0.20
    ) -> tuple[list[dict], list[dict], list[dict]]:
        if not 1 <= top_posts_per_issue <= 100:
            raise ValueError("top_posts_per_issue must be between 1 and 100")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        issues = self._load_issues()
        members = self._query(
            f"""
            SELECT m.id, m.slug, m.full_name_he, a.handle,
                   COUNT(p.id) AS post_count
            FROM {self.prefix}.mk` AS m
            JOIN {self.prefix}.social_post` AS p
              ON p.mk_id = m.id
             AND p.platform = 'twitter'
             AND NOT p.is_deleted
             AND p.text IS NOT NULL
             AND p.platform_post_id IS NOT NULL
            JOIN {self.prefix}.mk_social_account` AS a
              ON a.id = p.account_id
             AND a.platform = 'twitter'
            WHERE m.id = @politician
               OR CAST(m.knesset_member_id AS STRING) = @politician
               OR m.slug = @politician
               OR m.full_name_he = @politician
               OR LOWER(a.handle) = LOWER(@politician)
            GROUP BY m.id, m.slug, m.full_name_he, a.handle
            ORDER BY post_count DESC
            """,
            [bigquery.ScalarQueryParameter("politician", "STRING", politician)],
        )
        if not members:
            raise ValueError(f"no DB politician with usable Twitter posts matched {politician!r}")
        if len(members) > 1:
            matches = ", ".join(f"{row.full_name_he} (@{row.handle})" for row in members)
            raise ValueError(f"politician selector is ambiguous: {matches}")
        member = members[0]
        retrieved_rows = self._query(
            f"""
            WITH deduplicated AS (
              SELECT pi.issue_id, pi.post_id, MAX(pi.confidence) AS confidence
              FROM {self.prefix}.post_issue` AS pi
              JOIN {self.prefix}.social_post` AS p ON p.id = pi.post_id
              WHERE p.mk_id = @mk_id
                AND p.platform = 'twitter'
                AND NOT p.is_deleted
                AND p.text IS NOT NULL
                AND p.platform_post_id IS NOT NULL
                AND pi.confidence >= @min_confidence
              GROUP BY pi.issue_id, pi.post_id
            ),
            ranked AS (
              SELECT d.*,
                     ROW_NUMBER() OVER (
                       PARTITION BY d.issue_id
                       ORDER BY d.confidence DESC, p.posted_at DESC, d.post_id
                     ) AS retrieval_rank
              FROM deduplicated AS d
              JOIN {self.prefix}.social_post` AS p ON p.id = d.post_id
            )
            SELECT p.id, p.platform_post_id, p.url, p.posted_at, p.text,
                   p.engagement, a.handle, i.slug AS issue_slug,
                   r.confidence, r.retrieval_rank
            FROM ranked AS r
            JOIN {self.prefix}.social_post` AS p ON p.id = r.post_id
            JOIN {self.prefix}.mk_social_account` AS a
              ON a.id = p.account_id
             AND a.platform = 'twitter'
            JOIN {self.prefix}.issue` AS i ON i.id = r.issue_id
            WHERE r.retrieval_rank <= @top_n
            ORDER BY i.sort_order, r.retrieval_rank, p.id
            """,
            [
                bigquery.ScalarQueryParameter("mk_id", "STRING", member.id),
                bigquery.ScalarQueryParameter(
                    "top_n", "INT64", top_posts_per_issue
                ),
                bigquery.ScalarQueryParameter(
                    "min_confidence", "FLOAT64", min_confidence
                ),
            ],
        )

        retrieved_by_post: dict[str, dict[str, Any]] = {}
        retrieval_by_issue: dict[str, list[dict[str, Any]]] = {}
        for row in retrieved_rows:
            retrieval = {
                "issue": row.issue_slug,
                "confidence": float(row.confidence) if row.confidence is not None else None,
                "rank": row.retrieval_rank,
            }
            retrieval_by_issue.setdefault(row.issue_slug, []).append(
                {
                    "source": f"x:{row.handle}:{row.platform_post_id}",
                    "confidence": retrieval["confidence"],
                    "rank": row.retrieval_rank,
                }
            )
            if row.id not in retrieved_by_post:
                retrieved_by_post[row.id] = {
                    "row": row,
                    "retrievedForIssues": [],
                }
            retrieved_by_post[row.id]["retrievedForIssues"].append(retrieval)

        topics = [
            {
                "id": row.slug,
                "dbIssueId": row.id,
                "title": row.name or row.slug,
                "description": row.description or "",
                "analysisPrompt": row.prompt_for_social_post_similarity or "",
                "ratingScale": self._json_value(row.rating_scale),
                "retrieval": {
                    "strategy": "post_issue_confidence",
                    "topN": top_posts_per_issue,
                    "candidates": retrieval_by_issue.get(row.slug, []),
                },
                "axis": None,
            }
            for row in issues
        ]
        roster = [
            {
                "key": member.slug,
                "dbMkId": member.id,
                "name": member.full_name_he,
                "account": member.handle,
                "postCount": member.post_count,
            }
        ]
        posts = []
        for value in retrieved_by_post.values():
            row = value["row"]
            posts.append({
                "key": f"x:{row.handle}:{row.platform_post_id}",
                "dbPostId": row.id,
                "sourceType": "x",
                "publisher": member.full_name_he,
                "account": row.handle,
                "date": row.posted_at.date().isoformat() if row.posted_at else None,
                "createdAt": row.posted_at.isoformat() if row.posted_at else None,
                "text": row.text,
                "url": row.url
                or f"https://x.com/{row.handle}/status/{row.platform_post_id}",
                "metrics": self._json_value(row.engagement),
                "referencedTweets": [],
                "retrievedForIssues": value["retrievedForIssues"],
            })
        return topics, roster, posts

    def list_eligible_mks(self, skip_complete: bool = True) -> list[dict[str, Any]]:
        complete_filter = (
            "WHERE COALESCE(summary_coverage.issue_count, 0) < issue_total.issue_count"
            if skip_complete
            else ""
        )
        rows = self._query(
            f"""
            WITH eligible AS (
              SELECT
                m.id, m.slug, m.full_name_he, a.handle, COUNT(p.id) AS post_count
              FROM {self.prefix}.mk` AS m
              JOIN {self.prefix}.social_post` AS p
                ON p.mk_id = m.id
               AND p.platform = 'twitter'
               AND NOT p.is_deleted
               AND p.text IS NOT NULL
               AND p.platform_post_id IS NOT NULL
              JOIN {self.prefix}.mk_social_account` AS a
                ON a.id = p.account_id
               AND a.platform = 'twitter'
              GROUP BY m.id, m.slug, m.full_name_he, a.handle
            ),
            summary_coverage AS (
              SELECT mk_id, COUNT(DISTINCT issue_id) AS issue_count
              FROM {self.prefix}.mk_issue_summary`
              GROUP BY mk_id
            ),
            issue_total AS (
              SELECT COUNT(*) AS issue_count FROM {self.prefix}.issue`
            )
            SELECT eligible.*,
                   COALESCE(summary_coverage.issue_count, 0) AS summarized_issues
            FROM eligible
            LEFT JOIN summary_coverage ON summary_coverage.mk_id = eligible.id
            CROSS JOIN issue_total
            {complete_filter}
            ORDER BY eligible.full_name_he, eligible.id
            """
        )
        return [
            {
                "id": row.id,
                "slug": row.slug,
                "name": row.full_name_he,
                "handle": row.handle,
                "postCount": row.post_count,
                "summarizedIssues": row.summarized_issues,
            }
            for row in rows
        ]

    def _load_issues(self) -> list[Any]:
        if self._issues_cache is None:
            self._issues_cache = self._query(
                f"""
                SELECT id, slug, name, description,
                       prompt_for_social_post_similarity, rating_scale
                FROM {self.prefix}.issue`
                ORDER BY sort_order, slug
                """
            )
        return self._issues_cache

    @staticmethod
    def _json_value(value: Any) -> Any:
        if value is None or isinstance(value, (dict, list, str, int, float, bool)):
            return value
        return dict(value)
