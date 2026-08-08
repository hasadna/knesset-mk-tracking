import os
from mk_tracking.collect_x.bq_export import get_bigquery_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")
TABLE_ID = f"{PROJECT_ID}.mk_tracking.mk_social_account"

def main():
    client = get_bigquery_client(PROJECT_ID)

    dedup_sql = f"""
    CREATE OR REPLACE TABLE `{TABLE_ID}` AS
    SELECT * EXCEPT(rn)
    FROM (
        SELECT *, ROW_NUMBER() OVER(PARTITION BY mk_id, platform ORDER BY id) as rn
        FROM `{TABLE_ID}`
    )
    WHERE rn = 1
    """

    print("Deduplicating mk_social_account...")
    client.query(dedup_sql).result()
    print("Successfully deduplicated `mk_social_account`!")

if __name__ == "__main__":
    main()
