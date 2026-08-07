import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bq_export import get_bigquery_client

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

def fetch_missing_mks_from_bq():
    client = get_bigquery_client(PROJECT_ID)
    query = f"""
    SELECT 
        m.id AS mk_id, 
        m.knesset_member_id, 
        m.full_name_he, 
        m.full_name_en, 
        m.slug,
        m.is_current
    FROM `{PROJECT_ID}.mk_tracking.mk` m
    LEFT JOIN `{PROJECT_ID}.mk_tracking.mk_social_account` sa
      ON m.id = sa.mk_id AND sa.platform IN ('x', 'twitter')
    WHERE sa.handle IS NULL
    ORDER BY m.knesset_member_id DESC
    """
    results = list(client.query(query).result())
    return results

def fetch_all_wikidata_twitter_accounts():
    wikidata_query = """
    SELECT ?mk ?mkLabel_he ?mkLabel_en ?twitter WHERE {
      ?mk p:P39 ?statement .
      ?statement ps:P39 wd:Q4047513 .
      ?mk wdt:P2002 ?twitter .
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

    records = []
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            results = data["results"]["bindings"]
            for r in results:
                if "twitter" in r:
                    records.append({
                        "name_he": r.get("mkLabel_he", {}).get("value", "").strip(),
                        "name_en": r.get("mkLabel_en", {}).get("value", "").strip(),
                        "twitter": r["twitter"]["value"].strip().lstrip("@"),
                    })
    except Exception as err:
        print("Error querying Wikidata:", err)
    return records

def main():
    print("Fetching MKs without X account from BigQuery...")
    missing_mks = fetch_missing_mks_from_bq()
    print(f"Found {len(missing_mks)} total MKs in `mk` table without an X account in `mk_social_account`.")

    print("\nFetching all Wikidata Twitter handles for Israeli politicians/MKs...")
    wikidata_records = fetch_all_wikidata_twitter_accounts()
    print(f"Retrieved {len(wikidata_records)} Wikidata records with Twitter handles.")

    found_accounts = []
    
    # Matching logic
    for mk in missing_mks:
        db_he = (mk.full_name_he or "").strip()
        db_en = (mk.full_name_en or "").strip()
        
        matched_handle = None
        match_source = None

        for w_rec in wikidata_records:
            w_he = w_rec["name_he"]
            w_en = w_rec["name_en"]

            if db_he and db_he == w_he:
                matched_handle = w_rec["twitter"]
                match_source = "Wikidata exact HE"
                break
            if db_en and w_en and db_en.lower() == w_en.lower():
                matched_handle = w_rec["twitter"]
                match_source = "Wikidata exact EN"
                break

            # Partial/split matching in Hebrew
            if db_he and w_he:
                db_parts = db_he.split()
                w_parts = w_he.split()
                if len(db_parts) >= 2 and db_parts[0] in w_he and db_parts[-1] in w_he:
                    matched_handle = w_rec["twitter"]
                    match_source = "Wikidata name match"
                    break

        if matched_handle:
            # Avoid re-using handle if already matched
            if matched_handle in [x["handle"] for x in found_accounts]:
                continue

            found_accounts.append({
                "mk_id": mk.mk_id,
                "knesset_member_id": mk.knesset_member_id,
                "full_name_he": mk.full_name_he,
                "full_name_en": mk.full_name_en,
                "is_current": mk.is_current,
                "handle": matched_handle,
                "source": match_source,
            })

    with open("found_mks.json", "w", encoding="utf-8") as f:
        json.dump(found_accounts, f, ensure_ascii=False, indent=2)

    print("\n========================================================")
    print(f"MATCHED ACCOUNTS FOUND FOR UNMAPPED MKs ({len(found_accounts)} found)")
    print("Exported detailed results to found_mks.json")
    print("========================================================\n")

    for item in found_accounts:
        curr_str = "[CURRENT]" if item["is_current"] else "[HISTORICAL]"
        print(f"KM_ID: {item['knesset_member_id']:<5} | {curr_str:<12} | Name: {item['full_name_he']} ({item['full_name_en']}) | @{item['handle']} (via {item['source']})")

if __name__ == "__main__":
    main()
