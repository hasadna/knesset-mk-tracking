import json
import os
import sys
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mk_tracking.collect_x.bq_export import get_active_mk_twitter_accounts, get_bigquery_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

def populate():
    client = get_bigquery_client(PROJECT_ID)

    # Step 1: Clean up mock entries for Amit Segal from mk and mk_social_account tables
    print("1. Cleaning up mock entries for Amit Segal...")
    del_sa_sql = f"DELETE FROM `{PROJECT_ID}.mk_tracking.mk_social_account` WHERE handle = 'amit_segal'"
    client.query(del_sa_sql).result()

    del_mk_sql = f"DELETE FROM `{PROJECT_ID}.mk_tracking.mk` WHERE slug = 'amit-segal'"
    client.query(del_mk_sql).result()
    print("Cleaned up mock Amit Segal entries (social_post table remains untouched).")

    # Step 2: Fetch 25th Knesset active MKs with Twitter handles from Wikidata
    print("2. Fetching active MK Twitter handles from Wikidata...")
    wikidata_query = """
    SELECT ?mk ?mkLabel_he ?mkLabel_en ?twitter WHERE {
      ?mk p:P39 ?statement .
      ?statement ps:P39 wd:Q4047513 .
      ?statement pq:P2937 wd:Q114948813 .
      MINUS { ?statement pq:P582 ?end . }
      OPTIONAL { ?mk wdt:P2002 ?twitter . }
      OPTIONAL { ?mk rdfs:label ?mkLabel_he FILTER (lang(?mkLabel_he) = "he") }
      OPTIONAL { ?mk rdfs:label ?mkLabel_en FILTER (lang(?mkLabel_en) = "en") }
    }
    """
    url = "https://query.wikidata.org/sparql?query=" + urllib.parse.quote(wikidata_query)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "opencode-bot/1.0",
        },
    )

    wikidata_mks = []
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            results = data["results"]["bindings"]
            for r in results:
                if "twitter" in r:
                    wikidata_mks.append(
                        {
                            "name_he": r.get("mkLabel_he", {}).get("value", "").strip(),
                            "name_en": r.get("mkLabel_en", {}).get("value", "").strip(),
                            "twitter": r["twitter"]["value"].strip().lstrip("@"),
                        }
                    )
    except Exception as err:
        print("Error fetching from Wikidata:", err)
        return

    print(f"Retrieved {len(wikidata_mks)} active MKs with Twitter handles from Wikidata.")

    # Step 3: Fetch active MKs from BigQuery `mk` table
    print("3. Querying current MKs from BigQuery `mk` table...")
    mk_query = f"SELECT id, full_name_he, full_name_en, slug FROM `{PROJECT_ID}.mk_tracking.mk` WHERE is_current = TRUE"
    db_mks = list(client.query(mk_query).result())
    print(f"Retrieved {len(db_mks)} active MKs from BigQuery `mk` table.")

    # Step 4: Match and populate `mk_social_account`
    inserted_count = 0
    matched_handles = set()

    rows_to_insert = []
    for db_mk in db_mks:
        db_he = (db_mk.full_name_he or "").strip()
        db_en = (db_mk.full_name_en or "").strip()

        match = None
        for w_mk in wikidata_mks:
            w_he = w_mk["name_he"]
            w_en = w_mk["name_en"]

            # Direct Hebrew or English match
            if (db_he and db_he == w_he) or (db_en and db_en.lower() == w_en.lower()):
                match = w_mk
                break

            # Fallback split name matching (first and last name in Hebrew)
            if db_he and w_he:
                db_parts = db_he.split()
                if len(db_parts) >= 2 and db_parts[0] in w_he and db_parts[-1] in w_he:
                    match = w_mk
                    break

        if match and match["twitter"] not in matched_handles:
            handle = match["twitter"]
            social_uuid = str(uuid.uuid4())
            rows_to_insert.append({
                "id": social_uuid,
                "mk_id": db_mk.id,
                "platform": "twitter",
                "handle": handle,
                "url": f"https://x.com/{handle}",
                "is_active": True,
                "verified": True,
            })
            matched_handles.add(handle)

    if rows_to_insert:
        table_id = f"{PROJECT_ID}.mk_tracking.mk_social_account"
        job_config = get_bigquery_client().from_string_with_target if False else None
        job = client.load_table_from_json(rows_to_insert, table_id)
        job.result()
        inserted_count = len(rows_to_insert)

    print(f"Successfully inserted {inserted_count} Twitter accounts into `mk_social_account`.")

    # Step 5: Verify using get_active_mk_twitter_accounts
    active_accounts = get_active_mk_twitter_accounts(client)
    print(f"Verification: get_active_mk_twitter_accounts() returned {len(active_accounts)} active MK handles.")


if __name__ == "__main__":
    populate()
