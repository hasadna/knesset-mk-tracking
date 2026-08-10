"""Collect X Accounts CLI — Discover, inspect, and commit MK Twitter handles to BigQuery."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from google.cloud import bigquery

# Mapping of Knesset term numbers to Wikidata parliamentary term entities (wdt:P2937)
KNESSET_TERM_WIKIDATA: dict[int, str] = {
    25: "wd:Q114948813",  # 25th Knesset
    24: "wd:Q106091395",  # 24th Knesset
    23: "wd:Q87400346",   # 23rd Knesset
}


def get_bigquery_client(project_id: str | None = None) -> bigquery.Client:
    project = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        print("Error: GOOGLE_CLOUD_PROJECT environment variable or --project flag must be set.", file=sys.stderr)
        sys.exit(1)

    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path and not os.path.exists(cred_path):
        print(
            f"Error: GOOGLE_APPLICATION_CREDENTIALS points to a file that does not exist:\n"
            f"  {cred_path}\n\n"
            f"Please update GOOGLE_APPLICATION_CREDENTIALS in your environment or .env file to a valid key path,\n"
            f"or run: gcloud auth application-default login",
            file=sys.stderr,
        )
        sys.exit(1)

    token = os.environ.get("GCP_ACCESS_TOKEN") or os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    if token:
        from google.oauth2 import credentials as oauth_credentials
        creds = oauth_credentials.Credentials(token)
        return bigquery.Client(project=project, credentials=creds)

    try:
        return bigquery.Client(project=project)
    except Exception as err:
        print(f"Error initializing BigQuery client: {err}", file=sys.stderr)
        sys.exit(1)


def fetch_wikidata_twitter_accounts(knesset_term: int | None = None) -> list[dict[str, str]]:
    """Query Wikidata SPARQL for Knesset Members and their official Twitter handles."""
    term_filter_clause = ""
    if knesset_term is not None:
        wikidata_entity = KNESSET_TERM_WIKIDATA.get(knesset_term)
        if wikidata_entity:
            term_filter_clause = f"?statement pq:P2937 {wikidata_entity} ."
            print(f"ℹ️ Filtering Wikidata SPARQL for {knesset_term}th Knesset term ({wikidata_entity})")
        else:
            print(f"Warning: Knesset term {knesset_term} has no mapped Wikidata entity. Fetching all terms.", file=sys.stderr)

    wikidata_query = f"""
    SELECT ?mk ?mkLabel_he ?mkLabel_en ?twitter WHERE {{
      ?mk p:P39 ?statement .
      ?statement ps:P39 wd:Q4047513 .
      {term_filter_clause}
      MINUS {{ ?statement pq:P582 ?end . }}
      ?mk wdt:P2002 ?twitter .
      OPTIONAL {{ ?mk rdfs:label ?mkLabel_he FILTER (lang(?mkLabel_he) = "he") }}
      OPTIONAL {{ ?mk rdfs:label ?mkLabel_en FILTER (lang(?mkLabel_en) = "en") }}
    }}
    """
    url = "https://query.wikidata.org/sparql?query=" + urllib.parse.quote(wikidata_query)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": "knesset-mk-tracking/1.0",
        },
    )

    records: list[dict[str, str]] = []
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            results = data.get("results", {}).get("bindings", [])
            for r in results:
                if "twitter" in r:
                    handle = r["twitter"]["value"].strip().lstrip("@")
                    records.append({
                        "name_he": r.get("mkLabel_he", {}).get("value", "").strip(),
                        "name_en": r.get("mkLabel_en", {}).get("value", "").strip(),
                        "twitter": handle,
                    })
    except Exception as err:
        print(f"Warning: Error fetching from Wikidata SPARQL: {err}", file=sys.stderr)

    return records


def fetch_mks_from_bigquery(client: bigquery.Client, project_id: str, knesset_term: int | None = None) -> list[dict[str, Any]]:
    """Fetch MK records from BigQuery mk table. Defaults to all MKs unless knesset_term filter is applied."""
    where_clause = ""
    if knesset_term == 25:
        where_clause = "WHERE m.is_current = TRUE"

    query = f"""
    SELECT 
        m.id AS mk_id, 
        m.knesset_member_id, 
        m.full_name_he, 
        m.full_name_en, 
        m.slug,
        m.is_current,
        sa.handle AS existing_handle
    FROM `{project_id}.mk_tracking.mk` m
    LEFT JOIN `{project_id}.mk_tracking.mk_social_account` sa
      ON m.id = sa.mk_id AND sa.platform IN ('x', 'twitter') AND sa.is_active = TRUE
    {where_clause}
    ORDER BY m.knesset_member_id DESC
    """
    results = list(client.query(query).result())
    return [dict(row) for row in results]


def match_mks_with_wikidata(db_mks: list[dict[str, Any]], wikidata_mks: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Perform 3-tier name matching (Hebrew exact, English exact, First+Last split match)."""
    candidates: list[dict[str, Any]] = []
    seen_handles: set[str] = set()

    for db_mk in db_mks:
        db_he = (db_mk.get("full_name_he") or "").strip()
        db_en = (db_mk.get("full_name_en") or "").strip()
        existing_handle = db_mk.get("existing_handle")

        matched_handle = None
        match_source = None

        for w_mk in wikidata_mks:
            w_he = w_mk["name_he"]
            w_en = w_mk["name_en"]
            twitter = w_mk["twitter"]

            # 1. Exact Hebrew match
            if db_he and db_he == w_he:
                matched_handle = twitter
                match_source = "Wikidata exact HE"
                break
            # 2. Exact English match
            if db_en and w_en and db_en.lower() == w_en.lower():
                matched_handle = twitter
                match_source = "Wikidata exact EN"
                break
            # 3. Split Hebrew name match
            if db_he and w_he:
                db_parts = db_he.split()
                if len(db_parts) >= 2 and db_parts[0] in w_he and db_parts[-1] in w_he:
                    matched_handle = twitter
                    match_source = "Wikidata name match"
                    break

        handle_to_use = matched_handle or existing_handle
        if handle_to_use and handle_to_use not in seen_handles:
            candidates.append({
                "mk_id": str(db_mk["mk_id"]),
                "knesset_member_id": db_mk.get("knesset_member_id"),
                "full_name_he": db_he,
                "full_name_en": db_en,
                "is_current": db_mk.get("is_current"),
                "handle": handle_to_use,
                "existing_handle": existing_handle,
                "match_source": match_source or ("Existing DB Handle" if existing_handle else "Manual"),
                "status": "already_in_db" if existing_handle else "newly_discovered",
            })
            seen_handles.add(handle_to_use)

    return candidates


def export_candidate_file(candidates: list[dict[str, Any]], output_path: Path) -> None:
    """Save candidates to JSON file for manual inspection."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    print(f"✅ Exported {len(candidates)} candidate handle entries to: {output_path}")


def commit_candidates_to_bigquery(
    client: bigquery.Client,
    project_id: str,
    candidates: list[dict[str, Any]],
) -> None:
    """Insert missing candidate handle entries into BigQuery mk_social_account table."""
    table_id = f"{project_id}.mk_tracking.mk_social_account"
    existing_query = f"SELECT LOWER(handle) AS handle FROM `{table_id}` WHERE platform IN ('x', 'twitter')"
    existing_handles = {row.handle.lower() for row in client.query(existing_query).result()}

    rows_to_insert = []
    for item in candidates:
        handle = item.get("handle")
        if not handle:
            continue
        clean_handle = handle.strip().lstrip("@")
        if clean_handle.lower() in existing_handles:
            continue

        rows_to_insert.append({
            "id": str(uuid.uuid4()),
            "mk_id": item["mk_id"],
            "platform": "twitter",
            "handle": clean_handle,
            "url": f"https://x.com/{clean_handle}",
            "is_active": True,
            "verified": True,
        })

    if not rows_to_insert:
        print("ℹ️ No new handles to commit to `mk_social_account` (all handles already exist in DB).")
        return

    job = client.load_table_from_json(rows_to_insert, table_id)
    job.result()
    print(f"🚀 Successfully committed {len(rows_to_insert)} new X handles into `{table_id}`!")


def print_summary_table(candidates: list[dict[str, Any]]) -> None:
    """Print human-readable summary of candidate accounts."""
    newly_found = [c for c in candidates if c.get("status") == "newly_discovered"]
    existing = [c for c in candidates if c.get("status") == "already_in_db"]

    print("\n========================================================")
    print("📊 MK X (TWITTER) HANDLE MATCHING SUMMARY")
    print("========================================================")
    print(f"Total Accounts Evaluated : {len(candidates)}")
    print(f"Existing Handles in DB   : {len(existing)}")
    print(f"Newly Discovered Handles : {len(newly_found)}")
    print("========================================================\n")

    if newly_found:
        print("NEWLY DISCOVERED HANDLES FOR MANUAL INSPECTION:")
        for item in newly_found:
            curr_str = "[CURRENT]" if item.get("is_current") else "[HISTORICAL]"
            print(f"  - KM_ID: {item.get('knesset_member_id', 'N/A'):<5} | {curr_str:<12} | {item['full_name_he']} -> @{item['handle']} (via {item['match_source']})")
    else:
        print("All evaluated MKs already have handles linked in BigQuery `mk_social_account`.")
    print("")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover, inspect, and commit MK Twitter/X handles to BigQuery."
    )
    parser.add_argument(
        "--project",
        type=str,
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP Project ID for BigQuery access"
    )
    parser.add_argument(
        "--knesset",
        type=int,
        choices=[23, 24, 25],
        default=None,
        help="Filter by Knesset term index (e.g. 25 for 25th Knesset, mapped to Wikidata entity wd:Q114948813). Default: evaluate all MKs."
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("data/automatic/x_accounts_candidate.json"),
        help="Output JSON file for manual inspection (default: data/automatic/x_accounts_candidate.json)"
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=Path("data/automatic/x_accounts_candidate.json"),
        help="Input JSON file to commit to BigQuery (default: data/automatic/x_accounts_candidate.json)"
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit inspected candidate JSON file into BigQuery `mk_social_account`"
    )
    parser.add_argument(
        "--auto-commit",
        action="store_true",
        help="Directly discover and commit handles to BigQuery without stopping for inspection"
    )

    args = parser.parse_args()
    project_id = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT environment variable or --project flag must be set.", file=sys.stderr)
        sys.exit(1)

    client = get_bigquery_client(project_id)

    # 1. Manual Commit Mode (--commit flag specified without running discovery again)
    if args.commit and not args.auto_commit:
        if not args.input_file.exists():
            print(f"Error: Candidate file not found at {args.input_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.input_file, encoding="utf-8") as f:
            candidates: list[dict[str, Any]] = json.load(f)
        commit_candidates_to_bigquery(client, project_id, candidates)
        return

    # 2. Shared Discovery & Matching Step (Runs for both Default/Manual Inspection Mode & Auto-Commit Mode)
    print("🔍 Fetching MKs from BigQuery and querying Wikidata SPARQL...")
    db_mks = fetch_mks_from_bigquery(client, project_id, knesset_term=args.knesset)
    wikidata_mks = fetch_wikidata_twitter_accounts(knesset_term=args.knesset)

    print("🧩 Matching MK names with Wikidata handles...")
    candidates = match_mks_with_wikidata(db_mks, wikidata_mks)
    print_summary_table(candidates)

    # 3. Mode Branching: Auto-Commit vs. Default Manual Inspection
    if args.auto_commit:
        print("⚡ --auto-commit specified: Committing candidates to BigQuery directly in-memory...")
        commit_candidates_to_bigquery(client, project_id, candidates)
    else:
        # Default: Manual Inspection Mode — write file & exit for next run with --commit
        export_candidate_file(candidates, args.output_file)
        print("ℹ️ Inspect/edit the file above, then run with `--commit` to apply changes to BigQuery.")


if __name__ == "__main__":
    main()
