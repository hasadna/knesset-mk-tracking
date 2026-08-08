import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mk_tracking.collect_x.bq_export import get_active_mk_twitter_accounts, get_bigquery_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

def populate_mock():
    client = get_bigquery_client(PROJECT_ID)

    mk_uuid = str(uuid.uuid4())
    social_uuid = str(uuid.uuid4())

    mk_sql = f"""
    INSERT INTO `{PROJECT_ID}.mk_tracking.mk`
    (id, knesset_member_id, slug, full_name_he, full_name_en, is_current)
    VALUES
    ('{mk_uuid}', 9999, 'amit-segal', 'עמית סגל', 'Amit Segal', TRUE)
    """
    client.query(mk_sql).result()
    print(f"Successfully inserted MK Amit Segal with ID: {mk_uuid}")

    social_sql = f"""
    INSERT INTO `{PROJECT_ID}.mk_tracking.mk_social_account`
    (id, mk_id, platform, handle, url, is_active, verified)
    VALUES
    ('{social_uuid}', '{mk_uuid}', 'twitter', 'amit_segal', 'https://x.com/amit_segal', TRUE, TRUE)
    """
    client.query(social_sql).result()
    print(f"Successfully inserted Twitter account @amit_segal with ID: {social_uuid}")

    accounts = get_active_mk_twitter_accounts(client)
    print("Verification - Active MK accounts retrieved from BigQuery:", accounts)


if __name__ == "__main__":
    populate_mock()
