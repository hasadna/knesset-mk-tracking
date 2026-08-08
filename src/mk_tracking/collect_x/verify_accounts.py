import os
from mk_tracking.collect_x.bq_export import get_active_mk_twitter_accounts, get_bigquery_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

def main():
    client = get_bigquery_client(PROJECT_ID)
    
    query = f"""
    SELECT 
        COUNT(DISTINCT mk_id) AS mk_count, 
        COUNT(DISTINCT handle) AS handle_count 
    FROM `{PROJECT_ID}.mk_tracking.mk_social_account` 
    WHERE platform IN ('x', 'twitter')
    """
    res = list(client.query(query).result())[0]
    print(f"Total X accounts in mk_social_account: {res.handle_count} handles across {res.mk_count} unique MKs.")

    active_accounts = get_active_mk_twitter_accounts(client)
    print(f"Active sitting 25th Knesset MK Twitter handles: {len(active_accounts)}")

if __name__ == "__main__":
    main()
