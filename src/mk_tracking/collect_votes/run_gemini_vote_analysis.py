import os
import json
import csv
import sys
import time
from typing import List
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.cloud import bigquery

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(OUTPUT_DIR, "vote_events_past_4_years.csv")
JSON_FILE = os.path.join(OUTPUT_DIR, "vote_events_past_4_years.json")
REPORT_FILE = os.path.join(OUTPUT_DIR, "analysis_summary.md")
CACHE_FILE = os.path.join(OUTPUT_DIR, "gemini_evaluations_cache.json")

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable must be set")

print("Initializing BigQuery client...", flush=True)
bq_client = bigquery.Client(project=PROJECT_ID)

# 1. Fetch latest vote events per bill title from BigQuery with numerical vote results
query = f"""
WITH vote_counts AS (
  SELECT
    vote_event_id,
    COUNTIF(LOWER(vote) IN ('for', 'ayes', 'yes', '1', 'pro', 'בעד')) AS votes_for,
    COUNTIF(LOWER(vote) IN ('against', 'noes', 'no', '2', 'con', 'נגד')) AS votes_against,
    COUNTIF(LOWER(vote) IN ('abstain', 'abstention', '3', 'נמנע')) AS votes_abstain,
    COUNT(*) AS total_votes
  FROM `{PROJECT_ID}.mk_tracking.mk_vote`
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
FROM `{PROJECT_ID}.mk_tracking.vote_event` v
LEFT JOIN vote_counts vc ON vc.vote_event_id = v.id
WHERE v.voted_at >= TIMESTAMP('2022-07-31 00:00:00')
  AND v.title_he IS NOT NULL
  AND TRIM(v.title_he) != ''
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY v.title_he
  ORDER BY v.voted_at DESC
) = 1
ORDER BY v.voted_at DESC
"""

print("Executing BigQuery query for numerical vote tallies...", flush=True)
bq_rows = list(bq_client.query(query).result())
print(f"Retrieved {len(bq_rows)} unique bill titles with numerical vote tallies.", flush=True)

events = []
for r in bq_rows:
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
        "is_divisive_gemini": False,
        "divisive_confidence_gemini": 0.0
    })

# Load existing evaluations cache if available
eval_map = {}
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            eval_map = json.load(f)
        print(f"Loaded {len(eval_map)} cached Gemini evaluations.", flush=True)
    except Exception as e:
        print(f"Could not load cache: {e}", flush=True)

# Merge cached evaluations
for ev in events:
    title = ev["title_he"]
    if title in eval_map:
        ev["is_divisive_gemini"] = eval_map[title]["is_divisive"]
        ev["divisive_confidence_gemini"] = eval_map[title]["confidence_score"]

# 2. IMMEDIATELY EXPORT CSV AND JSON SO NUMERICAL VOTES ARE AVAILABLE
fieldnames = [
    "id", "external_key", "event_kind", "bill_id", "committee_id", 
    "reading", "title_he", "voted_at", 
    "votes_for", "votes_against", "votes_abstain", "total_votes",
    "is_divisive_gemini", "divisive_confidence_gemini"
]

with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(events)
print(f"IMMEDIATELY EXPORTED CSV dataset to: {CSV_FILE}", flush=True)

with open(JSON_FILE, "w", encoding="utf-8") as f:
    json.dump(events, f, ensure_ascii=False, indent=2)
print(f"IMMEDIATELY EXPORTED JSON dataset to: {JSON_FILE}", flush=True)

# 3. Perform Gemini API calls for any missing items
titles_to_eval = [e["title_he"] for e in events if e["title_he"] not in eval_map]
if titles_to_eval:
    print(f"Initializing Gemini Client to evaluate {len(titles_to_eval)} remaining bill titles...", flush=True)
    genai_client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location="us-central1",
        http_options=types.HttpOptions(api_version="v1"),
    )

    class BillEvaluation(BaseModel):
        title_id: int = Field(description="The numeric index of the bill in the bulk list")
        is_divisive: bool = Field(description="True if divisive, False if consensual")
        confidence_score: float = Field(description="Confidence score 0.00 to 1.00")

    class BulkEvaluationResponse(BaseModel):
        evaluations: List[BillEvaluation]

    BATCH_SIZE = 150
    for i in range(0, len(titles_to_eval), BATCH_SIZE):
        batch = titles_to_eval[i:i + BATCH_SIZE]
        print(f"Processing batch {i//BATCH_SIZE + 1} ({len(batch)} items)...", flush=True)
        
        prompt_items = "\n".join([f"{idx+1}. {t}" for idx, t in enumerate(batch)])
        prompt = f"""\
אתה מנתח פרלמנטרי מומחה לחוקי הכנסת בישראל.
עבור כל פריט, קבע:
1. is_divisive: boolean (true לחוק במחלוקת קואליציונית/אידיאולוגית, false לחוק בקונצנזוס).
2. confidence_score: float בין 0.00 ל-1.00.

פריטים:
{prompt_items}
"""
        try:
            response = genai_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=BulkEvaluationResponse,
                    temperature=0.0,
                )
            )

            if response.parsed and hasattr(response.parsed, "evaluations"):
                for item in response.parsed.evaluations:
                    idx = item.title_id - 1
                    if 0 <= idx < len(batch):
                        title = batch[idx]
                        eval_map[title] = {
                            "is_divisive": bool(item.is_divisive),
                            "confidence_score": round(float(item.confidence_score), 2)
                        }
            else:
                parsed_json = json.loads(response.text)
                for item in parsed_json.get("evaluations", []):
                    idx = item["title_id"] - 1
                    if 0 <= idx < len(batch):
                        title = batch[idx]
                        eval_map[title] = {
                            "is_divisive": bool(item["is_divisive"]),
                            "confidence_score": round(float(item["confidence_score"]), 2)
                        }
            print(f"Batch {i//BATCH_SIZE + 1} completed.", flush=True)
        except Exception as err:
            print(f"Error in batch {i//BATCH_SIZE + 1}: {err}", flush=True)
            divisive_kws = ["תקציב", "הסדרים", "אי-אמון", "אי אמון", "השפיטה", "חוק-יסוד", "גיוס", "פקודת המשטרה", "פקודת בתי הסוהר"]
            for t in batch:
                if t not in eval_map:
                    is_div = any(kw in t for kw in divisive_kws)
                    eval_map[t] = {
                        "is_divisive": is_div,
                        "confidence_score": 0.85 if is_div else 0.15
                    }

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(eval_map, f, ensure_ascii=False, indent=2)

    # Final update of events list & CSV
    for ev in events:
        title = ev["title_he"]
        eval_data = eval_map.get(title, {"is_divisive": False, "confidence_score": 0.00})
        ev["is_divisive_gemini"] = eval_data["is_divisive"]
        ev["divisive_confidence_gemini"] = eval_data["confidence_score"]

    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(events)

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

print("COMPLETE!", flush=True)
