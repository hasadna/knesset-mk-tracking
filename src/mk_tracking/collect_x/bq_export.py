import datetime
import json
import os
import uuid
from typing import Any

from google.cloud import bigquery
from google.oauth2 import credentials as oauth_credentials

_DEFAULT_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
DEFAULT_TABLE_ID = f"{_DEFAULT_PROJECT}.mk_tracking.social_post" if _DEFAULT_PROJECT else None


def get_bigquery_client(project_id: str | None = None) -> bigquery.Client:
    """Initialize BigQuery client supporting GCP_ACCESS_TOKEN or GOOGLE_APPLICATION_CREDENTIALS."""
    token = os.environ.get("GCP_ACCESS_TOKEN") or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    target_project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not target_project:
        raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

    if token:
        creds = oauth_credentials.Credentials(token)
        return bigquery.Client(project=target_project, credentials=creds)

    return bigquery.Client(project=target_project)


def get_active_mk_twitter_accounts(
    client: bigquery.Client | None = None,
    project_id: str | None = None,
) -> list[dict[str, str]]:
    """Query BigQuery mk and mk_social_account tables for current active MK Twitter handles."""
    if project_id is None:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")
    if client is None:
        client = get_bigquery_client(project_id=project_id)

    query = f"""
    SELECT 
        m.id AS mk_id, 
        a.id AS account_id, 
        a.handle
    FROM `{project_id}.mk_tracking.mk` m
    JOIN `{project_id}.mk_tracking.mk_social_account` a
      ON m.id = a.mk_id
    WHERE m.is_current = TRUE
      AND a.platform = 'twitter'
      AND a.is_active = TRUE
      AND a.handle IS NOT NULL
    """
    query_job = client.query(query)
    results = query_job.result()

    mk_accounts = []
    for row in results:
        clean_handle = row.handle.lstrip("@")
        mk_accounts.append({
            "mk_id": row.mk_id,
            "account_id": row.account_id,
            "handle": clean_handle,
        })
    return mk_accounts


def get_existing_social_post_mk_ids(
    client: bigquery.Client | None = None,
    table_id: str = DEFAULT_TABLE_ID,
) -> set[str]:
    """Query distinct mk_ids that already have posts in social_post table."""
    if client is None:
        client = get_bigquery_client(project_id=table_id.split(".")[0] if "." in table_id else None)

    query = f"SELECT DISTINCT mk_id FROM `{table_id}` WHERE platform = 'twitter'"
    try:
        query_job = client.query(query)
        results = query_job.result()
        return {row.mk_id for row in results if row.mk_id}
    except Exception:
        return set()


def get_mk_post_date_ranges(
    client: bigquery.Client | None = None,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, tuple[datetime.datetime, datetime.datetime]]:
    """Query BigQuery social_post table for min and max posted_at timestamps per mk_id."""
    if client is None:
        client = get_bigquery_client(project_id=table_id.split(".")[0] if "." in table_id else None)

    query = f"""
    SELECT 
        mk_id, 
        MIN(posted_at) AS min_posted_at, 
        MAX(posted_at) AS max_posted_at 
    FROM `{table_id}` 
    WHERE platform = 'twitter' AND posted_at IS NOT NULL
    GROUP BY mk_id
    """
    try:
        query_job = client.query(query)
        results = query_job.result()
        date_ranges = {}
        for row in results:
            if row.mk_id and row.min_posted_at and row.max_posted_at:
                date_ranges[row.mk_id] = (row.min_posted_at, row.max_posted_at)
        return date_ranges
    except Exception:
        return {}


def calculate_missing_date_intervals(
    requested_start_str: str | None,
    requested_end_str: str | None,
    existing_range: tuple[datetime.datetime, datetime.datetime] | None,
) -> list[tuple[str | None, str | None]]:
    """Calculate missing date intervals that are NOT covered by existing_range."""
    now_utc = datetime.datetime.now(datetime.UTC)

    def parse_dt(ts_str: str | None) -> datetime.datetime | None:
        if not ts_str:
            return None
        ts = ts_str.strip()
        if len(ts) == 10 and ts.count("-") == 2:
            ts = f"{ts}T00:00:00Z"
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            return dt
        except Exception:
            return None

    req_start = parse_dt(requested_start_str)
    req_end = parse_dt(requested_end_str) or now_utc

    if not existing_range:
        # No existing data at all -> full range is missing
        return [(requested_start_str, requested_end_str)]

    ex_min, ex_max = existing_range
    if ex_min.tzinfo is None:
        ex_min = ex_min.replace(tzinfo=datetime.UTC)
    if ex_max.tzinfo is None:
        ex_max = ex_max.replace(tzinfo=datetime.UTC)

    intervals = []

    # 1. Older missing interval (before existing data)
    if req_start is None or req_start < ex_min:
        older_end = min(req_end, ex_min)
        if req_start is None or req_start < older_end:
            older_start_str = requested_start_str
            older_end_str = older_end.isoformat().replace("+00:00", "Z")
            intervals.append((older_start_str, older_end_str))

    # 2. Newer missing interval (after existing data)
    if req_end > ex_max:
        newer_start = max(req_start if req_start else ex_max, ex_max)
        if newer_start < req_end:
            newer_start_str = newer_start.isoformat().replace("+00:00", "Z")
            newer_end_str = requested_end_str
            intervals.append((newer_start_str, newer_end_str))

    return intervals


def normalize_language(lang_code: str | None) -> str:
    """Map language code to allowed values: 'he', 'en', 'ar', 'other'."""
    if not lang_code:
        return "other"
    clean_lang = lang_code.lower()
    if clean_lang in ("iw", "he"):
        return "he"
    elif clean_lang == "en":
        return "en"
    elif clean_lang == "ar":
        return "ar"
    else:
        return "other"


def map_tweet_to_bq_row(
    tweet: dict[str, Any],
    account: str,
    user_id: str,
    fetched_at: str,
    mk_id_uuid: str | None = None,
    account_id_uuid: str | None = None,
) -> dict[str, Any]:
    """Map single tweet payload to BigQuery social_post schema row."""
    now_utc = datetime.datetime.now(datetime.UTC).isoformat()
    clean_handle = account.lstrip("@")
    tweet_id = tweet.get("id")
    tweet_url = f"https://x.com/{clean_handle}/status/{tweet_id}" if tweet_id else None

    # Text priority: full_text -> text
    tweet_text = tweet.get("full_text") or tweet.get("text") or ""

    # Public engagement metrics as JSON dict
    engagement_metrics = tweet.get("public_metrics", {})

    return {
        "id": str(uuid.uuid4()),
        "mk_id": mk_id_uuid or clean_handle,
        "account_id": account_id_uuid or user_id,
        "platform": "twitter",
        "platform_post_id": tweet_id,
        "url": tweet_url,
        "posted_at": tweet.get("created_at"),
        "text": tweet_text,
        "language": normalize_language(tweet.get("lang")),
        "engagement": json.dumps(engagement_metrics, ensure_ascii=False) if engagement_metrics else None,
        "is_deleted": False,
        "fetched_at": fetched_at,
        "created_at": now_utc,
    }


def push_tweets_to_bigquery(
    result_data: dict[str, Any],
    table_id: str = DEFAULT_TABLE_ID,
    mk_id_uuid: str | None = None,
    account_id_uuid: str | None = None,
) -> dict[str, Any]:
    """Push list of extracted tweets to Google BigQuery table via batch load job."""
    client = get_bigquery_client(project_id=table_id.split(".")[0] if "." in table_id else None)

    account = result_data.get("account", "")
    user_id = result_data.get("user_id", "")
    fetched_at = result_data.get("fetched_at", datetime.datetime.now(datetime.UTC).isoformat())
    tweets = result_data.get("tweets", [])

    if not tweets:
        return {
            "status": "skipped",
            "message": "No tweets to insert.",
            "inserted_rows": 0,
            "table_id": table_id,
        }

    rows_to_insert = [
        map_tweet_to_bq_row(tweet, account, user_id, fetched_at, mk_id_uuid=mk_id_uuid, account_id_uuid=account_id_uuid)
        for tweet in tweets
    ]

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = client.load_table_from_json(rows_to_insert, table_id, job_config=job_config)
    load_job.result()  # Wait for batch load job to complete

    return {
        "status": "success",
        "inserted_rows": len(rows_to_insert),
        "table_id": table_id,
    }
