import argparse
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import docx
import pymupdf as fitz
from google import genai
from google.cloud import bigquery, storage
from google.genai import types
from pydantic import BaseModel, Field
from tqdm import tqdm


class IssueScore(BaseModel):
    issue_id: str = Field(description="The exact Issue ID from the provided list")
    confidence: float = Field(description="The score from 0.0 to 1.0 indicating how much the bill relates to this issue")
    mapping_note: str = Field(description="Short explanation (1-2 sentences) of why this score was given")

class BillEvaluation(BaseModel):
    scores: list[IssueScore]

def get_issues(bq_client: bigquery.Client) -> list[dict]:
    query = """
    SELECT id, slug, name, description, prompt_for_bill_similarity 
    FROM mk_tracking.issue
    ORDER BY sort_order
    """
    results = bq_client.query(query).result()
    return [dict(row) for row in results]

def get_unprocessed_bills(bq_client: bigquery.Client, limit: int = None) -> list[dict]:
    query = """
    SELECT id, title_he, summary, enacted_law_document_uri
    FROM mk_tracking.bill b
    WHERE NOT EXISTS (
        SELECT 1 
        FROM mk_tracking.bill_issue bi 
        WHERE bi.bill_id = b.id
    )
    AND enacted_law_document_uri IS NOT NULL
    """
    if limit:
        query += f"\nLIMIT {limit}"
        
    results = bq_client.query(query).result()
    return [dict(row) for row in results]

def extract_text_from_gcs_uri(gcs_uri: str, storage_client: storage.Client) -> str:
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        return ""
        
    try:
        parts = gcs_uri[5:].split("/", 1)
        if len(parts) != 2:
            return ""
        bucket_name, blob_name = parts
        
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        content = blob.download_as_bytes()
        
        if blob_name.lower().endswith(".pdf"):
            doc = fitz.open(stream=content, filetype="pdf")
            text = ""
            for page in doc:
                text += page.get_text()
            return text
        elif blob_name.lower().endswith(".docx") or blob_name.lower().endswith(".doc"):
            try:
                doc = docx.Document(io.BytesIO(content))
                text = "\n".join([p.text for p in doc.paragraphs])
                return text
            except Exception:
                return ""
        else:
            return ""
    except Exception:
        return ""

def evaluate_bill(bill: dict, issues: list[dict], ai_client: genai.Client, full_text: str = "") -> tuple[list[IssueScore], int, int]:
    # Construct the issues text
    issues_text = ""
    for issue in issues:
        issues_text += f"- Issue ID: {issue['id']}\n"
        issues_text += f"  Name: {issue['name']}\n"
        if issue.get('description'):
            issues_text += f"  Description: {issue['description']}\n"
        if issue.get('prompt_for_bill_similarity'):
            issues_text += f"  Evaluation criteria: {issue['prompt_for_bill_similarity']}\n"
        issues_text += "\n"

    prompt = f"""You are an expert Israeli political analyst evaluating the content of legislative bills.
We have a predefined list of political/policy issues. Your task is to evaluate a single bill and determine how much it relates to each of these issues.

Score the bill against EACH issue on a scale of 0.0 to 1.0 (where 0.0 means the bill does not discuss this issue at all, and 1.0 means the bill is entirely or primarily about this issue).
Since a bill might touch on multiple issues, the scores do not have to sum to 1.0. 
For each issue, also provide a short explanation (1-2 sentences) of why you gave this score.
Do not skip any issue. Evaluate the bill against ALL issues listed below.

Here are the issues:
{issues_text}

Here is the bill to evaluate:
Title: {bill.get('title_he') or ''}
Summary: {bill.get('summary') or ''}
Full Content:
{full_text if full_text else '(No full content available, rely on the title/summary)'}

Return your output strictly as a JSON list of objects matching the requested schema.
"""

    response = ai_client.models.generate_content(
        model='gemini-3.6-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BillEvaluation,
            temperature=0.1
        )
    )
    
    input_tokens = 0
    output_tokens = 0
    if response.usage_metadata:
        input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
        output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0

    if not response.parsed:
        # Fallback if parsed is not populated (depends on library version)
        import json
        data = json.loads(response.text)
        scores = [IssueScore(**item) for item in data.get('scores', [])]
    else:
        scores = response.parsed.scores
        
    return scores, input_tokens, output_tokens

def write_scores_to_bq(bq_client: bigquery.Client, bill_id: str, scores: list[IssueScore]):
    rows_to_insert = []
    for score in scores:
        rows_to_insert.append({
            "bill_id": bill_id,
            "issue_id": score.issue_id,
            "mapping_method": "model",
            "mapping_note": score.mapping_note,
            "confidence": float(score.confidence)
        })
        
        if not rows_to_insert:
            return

        query = "INSERT INTO `mk_tracking.bill_issue` (bill_id, issue_id, mapping_method, mapping_note, confidence) VALUES "
        values = []
        params = []
        for i, row in enumerate(rows_to_insert):
            values.append(f"(@bill_id_{i}, @issue_id_{i}, @mapping_method_{i}, @mapping_note_{i}, @confidence_{i})")
            params.extend([
                bigquery.ScalarQueryParameter(f"bill_id_{i}", "STRING", row["bill_id"]),
                bigquery.ScalarQueryParameter(f"issue_id_{i}", "STRING", row["issue_id"]),
                bigquery.ScalarQueryParameter(f"mapping_method_{i}", "STRING", row["mapping_method"]),
                bigquery.ScalarQueryParameter(f"mapping_note_{i}", "STRING", row["mapping_note"]),
                bigquery.ScalarQueryParameter(f"confidence_{i}", "NUMERIC", row["confidence"]),
            ])
        query += ", ".join(values)

        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            bq_client.query(query, job_config=job_config).result()
        except Exception as e:
            raise Exception(f"Errors inserting into BQ: {e}")

def process_bill(bill: dict, issues: list[dict], ai_client: genai.Client, storage_client: storage.Client, bq_client: bigquery.Client):
    bill_id = bill['id']
    title = bill.get('title_he') or ''
    
    full_text = ""
    uri = bill.get('enacted_law_document_uri')
    if uri:
        full_text = extract_text_from_gcs_uri(uri, storage_client)
    
    try:
        scores, in_tokens, out_tokens = evaluate_bill(bill, issues, ai_client, full_text=full_text)
        
        # Basic validation
        returned_issue_ids = {s.issue_id for s in scores}
        expected_issue_ids = {i['id'] for i in issues}
        
        missing = expected_issue_ids - returned_issue_ids
        
        # Filter out any hallucinated issue IDs
        valid_scores = [s for s in scores if s.issue_id in expected_issue_ids]
        
        if valid_scores:
            write_scores_to_bq(bq_client, bill_id, valid_scores)
            
        return bill_id, in_tokens, out_tokens, missing, valid_scores, None
    except Exception as e:
        return bill_id, 0, 0, None, None, e

def main():
    parser = argparse.ArgumentParser(description="Populate bill_issue mapping using Gemini")
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of bills to process")
    parser.add_argument("--workers", type=int, default=10, help="Number of concurrent workers for parallel processing")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is missing.", file=sys.stderr)
        sys.exit(1)

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT environment variable is missing.", file=sys.stderr)
        sys.exit(1)
    gcp_token = os.environ.get("GCP_ACCESS_TOKEN")

    if gcp_token:
        from google.oauth2.credentials import Credentials
        creds = Credentials(gcp_token)
        bq_client = bigquery.Client(project=project_id, credentials=creds)
        storage_client = storage.Client(project=project_id, credentials=creds)
    else:
        bq_client = bigquery.Client(project=project_id)
        storage_client = storage.Client(project=project_id)
    ai_client = genai.Client(api_key=api_key)

    issues = get_issues(bq_client)
    if not issues:
        print("No issues found in mk_tracking.issue. Exiting.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(issues)} issues.")

    bills = get_unprocessed_bills(bq_client, limit=args.limit)
    if not bills:
        print("No unprocessed bills found. Exiting.")
        sys.exit(0)

    print(f"Found {len(bills)} unprocessed bills to evaluate.")
    print(f"Starting parallel processing with {args.workers} workers...")

    total_input_tokens = 0
    total_output_tokens = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_bill, bill, issues, ai_client, storage_client, bq_client): bill for bill in bills}
        
        with tqdm(total=len(bills), desc="Processing bills", unit="bill") as pbar:
            for future in as_completed(futures):
                bill_id, in_tokens, out_tokens, missing, valid_scores, error = future.result()
                
                total_input_tokens += in_tokens
                total_output_tokens += out_tokens
                
                if error:
                    tqdm.write(f"Error evaluating bill {bill_id}: {error}")
                else:
                    if missing:
                        tqdm.write(f"Warning: The model skipped {len(missing)} issues for bill {bill_id}.")
                    if not valid_scores:
                        tqdm.write(f"Warning: No valid scores parsed for {bill_id}")
                
                pbar.update(1)
            
    print("\n" + "="*50)
    print("TOKEN USAGE & COST ESTIMATION")
    print("="*50)
    print(f"Total Bills Processed: {len(bills)}")
    print(f"Total Input Tokens:    {total_input_tokens:,}")
    print(f"Total Output Tokens:   {total_output_tokens:,}")
    
    # Using typical Gemini Flash pricing: $0.075 / 1M input, $0.30 / 1M output
    INPUT_PRICE_PER_M = 0.075
    OUTPUT_PRICE_PER_M = 0.30
    
    cost_input = (total_input_tokens / 1_000_000) * INPUT_PRICE_PER_M
    cost_output = (total_output_tokens / 1_000_000) * OUTPUT_PRICE_PER_M
    total_cost = cost_input + cost_output
    
    print("Estimated Cost:")
    print(f"  Input:  ${cost_input:.6f}")
    print(f"  Output: ${cost_output:.6f}")
    print(f"  Total:  ${total_cost:.6f} USD")
    print("="*50)

if __name__ == "__main__":
    main()
