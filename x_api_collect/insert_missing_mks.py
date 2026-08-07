import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bq_export import get_bigquery_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")
SOCIAL_ACCOUNT_TABLE = f"{PROJECT_ID}.mk_tracking.mk_social_account"

def main():
    client = get_bigquery_client(PROJECT_ID)

    if not os.path.exists("found_mks.json"):
        print("Error: found_mks.json not found. Run find_missing_mks.py first.")
        return

    with open("found_mks.json", encoding="utf-8") as f:
        found_accounts = json.load(f)

    print(f"Loaded {len(found_accounts)} matched accounts from found_mks.json.")

    # Step 1: Query existing social accounts from BigQuery to ensure idempotency
    existing_query = f"SELECT mk_id, LOWER(handle) AS handle FROM `{SOCIAL_ACCOUNT_TABLE}` WHERE platform IN ('x', 'twitter')"
    existing_rows = list(client.query(existing_query).result())
    
    existing_mk_ids = {r.mk_id for r in existing_rows if r.mk_id}
    existing_handles = {r.handle for r in existing_rows if r.handle}

    print(f"BigQuery current state: {len(existing_mk_ids)} unique MKs with accounts, {len(existing_handles)} existing handles.")

    # Step 2: Filter found_accounts to only include accounts NOT YET present by mk_id OR handle
    rows_to_insert = []
    skipped_count = 0

    for item in found_accounts:
        mk_id = item["mk_id"]
        handle = item["handle"].lstrip("@").strip()
        handle_lower = handle.lower()

        if mk_id in existing_mk_ids:
            print(f"Skipping KM {item['knesset_member_id']} ({item['full_name_he']}): MK already has an account in mk_social_account.")
            skipped_count += 1
            continue

        if handle_lower in existing_handles:
            print(f"Skipping handle @{handle} for KM {item['knesset_member_id']} ({item['full_name_he']}): Handle already assigned to another MK.")
            skipped_count += 1
            continue

        # Prepare new social account row
        social_id = str(uuid.uuid4())
        rows_to_insert.append({
            "id": social_id,
            "mk_id": mk_id,
            "platform": "twitter",
            "handle": handle,
            "url": f"https://x.com/{handle}",
            "is_active": True,
            "verified": True,
        })
        existing_mk_ids.add(mk_id)
        existing_handles.add(handle_lower)

    print(f"\nFiltered: {len(rows_to_insert)} new accounts ready to insert ({skipped_count} skipped as already present).")

    if not rows_to_insert:
        print("No new accounts to insert.")
        return

    # Step 3: Batch load into BigQuery
    print(f"Batch inserting {len(rows_to_insert)} accounts into {SOCIAL_ACCOUNT_TABLE}...")
    job = client.load_table_from_json(rows_to_insert, SOCIAL_ACCOUNT_TABLE)
    job.result()

    print(f"Successfully inserted {len(rows_to_insert)} new X accounts into `mk_social_account`!")

if __name__ == "__main__":
    main()
