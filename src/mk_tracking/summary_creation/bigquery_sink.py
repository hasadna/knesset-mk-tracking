from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from .bigquery_source import BigQuerySource


class BigQuerySink:
    """Atomically publish one MK's validated summaries and supporting posts."""

    def __init__(self, project: str, dataset: str) -> None:
        self.source = BigQuerySource(project, dataset)
        self.client = self.source.client
        self.project = project
        self.dataset = dataset

    @property
    def prefix(self) -> str:
        return f"`{self.project}.{self.dataset}"

    def publish(
        self,
        artifact: list[dict],
        member: dict,
        posts: list[dict],
        model_version: str,
        generation_elapsed_seconds: float | None,
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        post_ids = {post["key"]: post["dbPostId"] for post in posts}
        summaries: list[dict[str, Any]] = []
        supporting: list[dict[str, str]] = []
        for topic in artifact:
            opinion = topic["members"][member["name"]]
            summaries.append(
                {
                    "issueId": topic["dbIssueId"],
                    "summary": opinion["stance"],
                    "extendedSummary": opinion["extendedStance"],
                    "quality": opinion["status"],
                    "rating": opinion["rating"],
                    "limitations": opinion["limitations"],
                }
            )
            for source in opinion["sources"]:
                if source not in post_ids:
                    raise ValueError(f"cannot publish unknown DB source {source}")
                supporting.append(
                    {"issueId": topic["dbIssueId"], "postId": post_ids[source]}
                )

        self._insert_run(
            run_id,
            member,
            model_version,
            started_at,
            len(summaries),
            len(posts),
        )
        publish_started = time.perf_counter()
        try:
            self._publish_transaction(
                run_id, member["dbMkId"], summaries, supporting, model_version
            )
            publish_seconds = time.perf_counter() - publish_started
            self._finish_run(
                run_id,
                "completed",
                generation_elapsed_seconds,
                None,
            )
        except BaseException as error:
            self._finish_run(
                run_id,
                "failed",
                generation_elapsed_seconds,
                str(error)[:5000],
            )
            raise
        return {
            "runId": run_id,
            "summaryCount": len(summaries),
            "supportingPostCount": len(supporting),
            "publishElapsedSeconds": round(publish_seconds, 3),
        }

    def _insert_run(
        self,
        run_id: str,
        member: dict,
        model_version: str,
        started_at: datetime,
        issue_count: int,
        post_count: int,
    ) -> None:
        sql = f"""
        INSERT INTO {self.prefix}.summary_generation_run`
          (id, mk_id, model_version, started_at, status, issue_count, post_count)
        VALUES
          (@id, @mk_id, @model, @started_at, 'running', @issues, @posts)
        """
        parameters = [
            bigquery.ScalarQueryParameter("id", "STRING", run_id),
            bigquery.ScalarQueryParameter("mk_id", "STRING", member["dbMkId"]),
            bigquery.ScalarQueryParameter("model", "STRING", model_version),
            bigquery.ScalarQueryParameter("started_at", "TIMESTAMP", started_at),
            bigquery.ScalarQueryParameter("issues", "INT64", issue_count),
            bigquery.ScalarQueryParameter("posts", "INT64", post_count),
        ]
        self.source._query(sql, parameters)

    def _publish_transaction(
        self,
        run_id: str,
        mk_id: str,
        summaries: list[dict[str, Any]],
        supporting: list[dict[str, str]],
        model_version: str,
    ) -> None:
        sql = f"""
        BEGIN TRANSACTION;

        MERGE {self.prefix}.mk_issue_summary` AS target
        USING (
          SELECT
            JSON_VALUE(item, '$.issueId') AS issue_id,
            JSON_VALUE(item, '$.summary') AS summary_he,
            JSON_VALUE(item, '$.extendedSummary') AS extended_summary_he,
            JSON_VALUE(item, '$.quality') AS quality,
            SAFE_CAST(JSON_VALUE(item, '$.rating') AS INT64) AS rating,
            JSON_VALUE(item, '$.limitations') AS limitations
          FROM UNNEST(JSON_QUERY_ARRAY(PARSE_JSON(@summaries_json))) AS item
        ) AS source
        ON target.mk_id = @mk_id AND target.issue_id = source.issue_id
        WHEN MATCHED THEN UPDATE SET
          summary_he = source.summary_he,
          extended_summary_he = source.extended_summary_he,
          quality = source.quality,
          rating = source.rating,
          limitations = source.limitations,
          model_version = @model,
          generation_run_id = @run_id,
          updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
          (id, mk_id, issue_id, summary_he, extended_summary_he, quality, rating,
           limitations, model_version, generation_run_id, updated_at)
        VALUES
          (GENERATE_UUID(), @mk_id, source.issue_id, source.summary_he,
           source.extended_summary_he, source.quality, source.rating,
           source.limitations,
           @model, @run_id, CURRENT_TIMESTAMP());

        DELETE FROM {self.prefix}.mk_issue_summary_supporting_post`
        WHERE summary_id IN (
          SELECT id
          FROM {self.prefix}.mk_issue_summary`
          WHERE mk_id = @mk_id
            AND issue_id IN (
              SELECT JSON_VALUE(item, '$.issueId')
              FROM UNNEST(JSON_QUERY_ARRAY(PARSE_JSON(@summaries_json))) AS item
            )
        );

        INSERT INTO {self.prefix}.mk_issue_summary_supporting_post`
          (summary_id, post_id)
        SELECT summary.id, JSON_VALUE(link, '$.postId')
        FROM UNNEST(JSON_QUERY_ARRAY(PARSE_JSON(@supporting_json))) AS link
        JOIN {self.prefix}.mk_issue_summary` AS summary
          ON summary.mk_id = @mk_id
         AND summary.issue_id = JSON_VALUE(link, '$.issueId');

        COMMIT TRANSACTION;
        """
        parameters = [
            bigquery.ScalarQueryParameter("mk_id", "STRING", mk_id),
            bigquery.ScalarQueryParameter("model", "STRING", model_version),
            bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
            bigquery.ScalarQueryParameter(
                "summaries_json",
                "STRING",
                json.dumps(summaries, ensure_ascii=False),
            ),
            bigquery.ScalarQueryParameter(
                "supporting_json",
                "STRING",
                json.dumps(supporting, ensure_ascii=False),
            ),
        ]
        self.source._query(sql, parameters)

    def _finish_run(
        self,
        run_id: str,
        status: str,
        generation_elapsed_seconds: float | None,
        error_message: str | None,
    ) -> None:
        elapsed_ms = (
            round(generation_elapsed_seconds * 1000)
            if generation_elapsed_seconds is not None
            else None
        )
        sql = f"""
        UPDATE {self.prefix}.summary_generation_run`
        SET completed_at = CURRENT_TIMESTAMP(),
            elapsed_ms = @elapsed_ms,
            status = @status,
            error_message = @error
        WHERE id = @id
        """
        parameters = [
            bigquery.ScalarQueryParameter("elapsed_ms", "INT64", elapsed_ms),
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("error", "STRING", error_message),
            bigquery.ScalarQueryParameter("id", "STRING", run_id),
        ]
        self.source._query(sql, parameters)
