import argparse
import csv
import json
import os
import sys
from typing import List, Optional

from google import genai
from google.cloud import bigquery
from pydantic import BaseModel, Field


class VoteDivisivenessEval(BaseModel):
    is_divisive: bool = Field(description="Whether this vote reflects significant political or ideological conflict between coalition and opposition")
    confidence_score: float = Field(description="Confidence score from 0.0 to 1.0")
    explanation: str = Field(description="Short explanation of the vote's significance")


def fetch_vote_events(
    bq_client: bigquery.Client,
    project_id: str,
    start_date: str = "2022-01-01",
    end_date: Optional[str] = None
) -> List[dict]:
    """Fetch deduplicated vote events from BigQuery for a given date range."""
    date_filter = f"v.voted_at >= TIMESTAMP('{start_date} 00:00:00')"
    if end_date:
        date_filter += f" AND v.voted_at <= TIMESTAMP('{end_date} 23:59:59')"

    query = f"""
    WITH vote_counts AS (
      SELECT
        vote_event_id,
        COUNTIF(LOWER(vote) IN ('for', 'ayes', 'yes', '1', 'pro', 'בעד')) AS votes_for,
        COUNTIF(LOWER(vote) IN ('against', 'noes', 'no', '2', 'con', 'נגד')) AS votes_against,
        COUNTIF(LOWER(vote) IN ('abstain', 'abstention', '3', 'נמנע')) AS votes_abstain,
        COUNT(*) AS total_votes
      FROM `{project_id}.mk_tracking.mk_vote`
      GROUP BY vote_event_id
    )
    SELECT
      v.id,
      v.external_key,
      v.event_kind,
      v.bill_id,
      v.committee_id,
      v.reading,
      v.title_he,
      v.voted_at,
      COALESCE(vc.votes_for, 0) AS votes_for,
      COALESCE(vc.votes_against, 0) AS votes_against,
      COALESCE(vc.votes_abstain, 0) AS votes_abstain,
      COALESCE(vc.total_votes, 0) AS total_votes
    FROM `{project_id}.mk_tracking.vote_event` v
    LEFT JOIN vote_counts vc ON vc.vote_event_id = v.id
    WHERE {date_filter}
      AND v.title_he IS NOT NULL
      AND TRIM(v.title_he) != ''
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY v.title_he
      ORDER BY v.voted_at DESC
    ) = 1
    ORDER BY v.voted_at DESC
    """
    print(f"Fetching vote events from BigQuery (Start Date: {start_date})...", flush=True)
    rows = list(bq_client.query(query).result())
    
    events = []
    for r in rows:
        events.append({
            "id": r.id,
            "external_key": r.external_key or "",
            "event_kind": r.event_kind or "",
            "bill_id": r.bill_id or "",
            "committee_id": r.committee_id or "",
            "reading": r.reading if r.reading is not None else "",
            "title_he": r.title_he or "",
            "voted_at": r.voted_at.strftime("%Y-%m-%d %H:%M:%S") if r.voted_at else "",
            "votes_for": r.votes_for,
            "votes_against": r.votes_against,
            "votes_abstain": r.votes_abstain,
            "total_votes": r.total_votes,
        })
    print(f"Retrieved {len(events)} unique vote events.", flush=True)
    return events


def evaluate_divisiveness_with_gemini(events: List[dict], gemini_client: genai.Client) -> List[dict]:
    """Uses Gemini 3.6 Flash to evaluate political divisiveness for each vote event based on title and tallies."""
    print(f"Evaluating political divisiveness with Gemini for {len(events)} vote events...", flush=True)
    
    for ev in events:
        title = ev["title_he"]
        votes_for = ev["votes_for"]
        votes_against = ev["votes_against"]
        
        # Heuristic fast-pass: unanimous or 0 against is rarely divisive
        if votes_against == 0 and votes_for > 10:
            ev["is_divisive_gemini"] = False
            ev["divisive_confidence_gemini"] = 0.95
            ev["divisive_explanation"] = "Unanimous vote with zero opposing votes."
            continue

        prompt = f"""Evaluate whether the following Israeli Knesset vote event represents a politically controversial/divisive vote (coalition vs opposition battle, major reform, budget) or a routine consensus vote.

Bill / Vote Title: {title}
Votes For (בעד): {votes_for}
Votes Against (נגד): {votes_against}
Abstentions (נמנעים): {ev['votes_abstain']}
Total Votes: {ev['total_votes']}
"""
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": VoteDivisivenessEval,
                }
            )
            eval_res = VoteDivisivenessEval.model_validate_json(response.text)
            ev["is_divisive_gemini"] = eval_res.is_divisive
            ev["divisive_confidence_gemini"] = eval_res.confidence_score
            ev["divisive_explanation"] = eval_res.explanation
        except Exception as err:
            print(f"Warning: Gemini evaluation failed for title '{title}': {err}")
            ev["is_divisive_gemini"] = False
            ev["divisive_confidence_gemini"] = 0.0
            ev["divisive_explanation"] = "Evaluation error"

    return events


def export_datasets(events: List[dict], export_dir: str):
    """Export vote events dataset into JSON and CSV files."""
    os.makedirs(export_dir, exist_ok=True)
    json_path = os.path.join(export_dir, "vote_events.json")
    csv_path = os.path.join(export_dir, "vote_events.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    print(f"Exported JSON dataset to: {json_path}")

    if events:
        fieldnames = list(events[0].keys())
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(events)
        print(f"Exported CSV dataset to: {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="Analyze historical Knesset voting events dataset")
    parser.add_argument(
        "--project",
        type=str,
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP Project ID for BigQuery access"
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2022-01-01",
        help="Start date filter (YYYY-MM-DD). Default: 2022-01-01"
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Optional end date filter (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "data"),
        help="Output directory for CSV/JSON datasets"
    )
    parser.add_argument(
        "--classify-divisiveness",
        action="store_true",
        help="Run Gemini AI divisiveness scoring on vote events"
    )
    args = parser.parse_args()

    project_id = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT environment variable or --project flag must be set.", file=sys.stderr)
        sys.exit(1)

    bq_client = bigquery.Client(project=project_id)
    events = fetch_vote_events(
        bq_client,
        project_id,
        start_date=args.start_date,
        end_date=args.end_date
    )

    if args.classify_divisiveness:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY environment variable required for --classify-divisiveness", file=sys.stderr)
            sys.exit(1)
        gemini_client = genai.Client(api_key=api_key)
        events = evaluate_divisiveness_with_gemini(events, gemini_client)

    export_datasets(events, args.export_dir)
    print("Voting analysis dataset export complete.")


if __name__ == "__main__":
    main()
