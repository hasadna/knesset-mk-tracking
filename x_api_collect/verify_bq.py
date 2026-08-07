import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bq_export import get_bigquery_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

def verify():
    client = get_bigquery_client(PROJECT_ID)
    query = f"""
    SELECT mk_id, platform, platform_post_id, posted_at, text, language
    FROM `{PROJECT_ID}.mk_tracking.social_post`
    WHERE mk_id = '274cbf3f-bbbf-4e2c-b416-36e9171cccb4'
    ORDER BY posted_at DESC
    """
    results = list(client.query(query).result())
    print(f"Total rows verified in BigQuery social_post table: {len(results)}\n")
    for idx, r in enumerate(results, 1):
        print(f"{idx}. Post ID: {r.platform_post_id} | Posted At: {r.posted_at} | Lang: {r.language}")
        print(f"   Text: {r.text[:120]}...\n")


if __name__ == "__main__":
    verify()
