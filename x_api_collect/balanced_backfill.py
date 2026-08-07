"""Backfill an exact number of older, unseen X posts for well-covered entities."""

import argparse
import datetime
import json
import os
import pathlib
import uuid

from google.cloud import bigquery
from x_client import XApiClient

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")


def write_progress(path: pathlib.Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument(
        "--exclude-bottom-percent",
        type=int,
        default=20,
        choices=range(100),
        metavar="0-99",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--progress", required=True)
    parser.add_argument(
        "--retry-errors-from",
        help="Process only handles whose prior progress record contains an error.",
    )
    args = parser.parse_args()

    output = pathlib.Path(args.output)
    progress_path = pathlib.Path(args.progress)
    output.parent.mkdir(parents=True, exist_ok=True)
    bq = bigquery.Client(project=PROJECT)

    candidates_sql = f"""
    WITH counts AS (
      SELECT
        m.id AS mk_id,
        m.knesset_member_id,
        m.full_name_he,
        m.is_current,
        COUNT(DISTINCT sp.platform_post_id) AS tweet_count,
        MIN(sp.posted_at) AS earliest_posted_at
      FROM `{PROJECT}.mk_tracking.mk` AS m
      JOIN `{PROJECT}.mk_tracking.social_post` AS sp
        ON sp.mk_id = m.id AND sp.platform = 'twitter'
      GROUP BY m.id, m.knesset_member_id, m.full_name_he, m.is_current
    ),
    cutoffs AS (
      SELECT
        is_current,
        APPROX_QUANTILES(tweet_count, 100)[OFFSET({args.exclude_bottom_percent})] AS cutoff
      FROM counts
      GROUP BY is_current
    )
    SELECT
      counts.*,
      account.id AS account_id,
      account.handle
    FROM counts
    JOIN cutoffs USING (is_current)
    JOIN `{PROJECT}.mk_tracking.mk_social_account` AS account
      ON account.mk_id = counts.mk_id
      AND account.platform = 'twitter'
      AND account.is_active
    WHERE counts.tweet_count >= cutoffs.cutoff
      AND account.id IS NOT NULL
      AND account.handle IS NOT NULL
    ORDER BY counts.is_current DESC, counts.tweet_count DESC, counts.mk_id
    """
    candidates = [dict(row) for row in bq.query(candidates_sql).result()]
    if args.retry_errors_from:
        prior = json.loads(
            pathlib.Path(args.retry_errors_from).read_text(encoding="utf-8")
        )
        retry_handles = {
            item["handle"].lower()
            for item in prior.get("summaries", [])
            if item.get("error")
        }
        candidates = [
            item for item in candidates if item["handle"].lstrip("@").lower() in retry_handles
        ]
    existing = {
        str(row.platform_post_id)
        for row in bq.query(
            f"""SELECT platform_post_id
                FROM `{PROJECT}.mk_tracking.social_post`
                WHERE platform = 'twitter' AND platform_post_id IS NOT NULL"""
        ).result()
    }

    client = XApiClient()
    summaries: list[dict] = []
    batch_seen: set[str] = set()
    total_rows = 0
    output.write_text("", encoding="utf-8")

    with output.open("a", encoding="utf-8", newline="\n") as stream:
        for index, account in enumerate(candidates, start=1):
            handle = account["handle"].lstrip("@")
            collected: list[dict] = []
            pages = 0
            exhausted = False
            error = None
            try:
                user_id, user_response = client.get_user_data_by_username(handle)
                canonical_handle = user_response["data"]["username"]
                token = None
                while len(collected) < args.count and pages < 20:
                    params = {
                        "max_results": max(5, min(100, args.count)),
                        "end_time": account["earliest_posted_at"].isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "tweet.fields": (
                            "created_at,text,note_tweet,public_metrics,author_id,lang,"
                            "conversation_id,entities,geo,in_reply_to_user_id,"
                            "referenced_tweets,source"
                        ),
                        "expansions": (
                            "referenced_tweets.id,referenced_tweets.id.author_id"
                        ),
                        "user.fields": "username,name",
                    }
                    if token:
                        params["pagination_token"] = token
                    raw = client._request("GET", f"/users/{user_id}/tweets", params=params)
                    pages += 1
                    includes = raw.get("includes", {})
                    tweets_by_id = {item["id"]: item for item in includes.get("tweets", [])}
                    users_by_id = {item["id"]: item for item in includes.get("users", [])}
                    for tweet in raw.get("data", []):
                        tweet_id = str(tweet["id"])
                        if tweet_id in existing or tweet_id in batch_seen:
                            continue
                        tweet["full_text"] = client._extract_full_text(
                            tweet, tweets_by_id, users_by_id
                        )
                        collected.append(tweet)
                        batch_seen.add(tweet_id)
                        if len(collected) == args.count:
                            break
                    token = raw.get("meta", {}).get("next_token")
                    if not token:
                        exhausted = True
                        break

                fetched_at = datetime.datetime.now(datetime.UTC).isoformat()
                for tweet in collected:
                    tweet_id = str(tweet["id"])
                    language = (tweet.get("lang") or "other").lower()
                    language = "he" if language == "iw" else language
                    if language not in {"he", "en", "ar"}:
                        language = "other"
                    row = {
                        "id": str(uuid.uuid4()),
                        "mk_id": account["mk_id"],
                        "account_id": account["account_id"],
                        "platform": "twitter",
                        "platform_post_id": tweet_id,
                        "url": f"https://x.com/{canonical_handle}/status/{tweet_id}",
                        "posted_at": tweet.get("created_at"),
                        "text": tweet.get("full_text") or tweet.get("text") or "",
                        "language": language,
                        "engagement": tweet.get("public_metrics") or None,
                        "is_deleted": False,
                        "fetched_at": fetched_at,
                        "created_at": fetched_at,
                    }
                    stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                    stream.write("\n")
                stream.flush()
                total_rows += len(collected)
            except Exception as exc:  # continue other accounts and report precisely
                error = str(exc)

            summaries.append(
                {
                    "handle": handle,
                    "name": account["full_name_he"],
                    "is_current": account["is_current"],
                    "previous_distinct": account["tweet_count"],
                    "collected": len(collected),
                    "pages": pages,
                    "exhausted": exhausted,
                    "error": error,
                }
            )
            write_progress(
                progress_path,
                {
                    "status": "running",
                    "processed": index,
                    "targeted": len(candidates),
                    "rows": total_rows,
                    "summaries": summaries,
                },
            )

    write_progress(
        progress_path,
        {
            "status": "complete",
            "processed": len(candidates),
            "targeted": len(candidates),
            "rows": total_rows,
            "summaries": summaries,
        },
    )


if __name__ == "__main__":
    main()
