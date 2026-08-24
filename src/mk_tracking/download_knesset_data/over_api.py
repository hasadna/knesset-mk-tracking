"""Read-only client for the Over Knesset API (https://www.over.org.il).

The public record: MKs, parties, affiliations, bills, vote events and votes.
Every function here does HTTP and returns plain dicts — no database of any kind
is involved, so ingestion into whatever store comes next stays a separate
concern. This module was extracted from the retired BigQuery uploader, which
was the only reason these fetchers ever lived next to warehouse code.

Pagination & rate limits:
  * The API caps a single SQL response at 1,000 rows; `fetch_all_over_sql`
    paginates with `LIMIT 1000 OFFSET X` until a short page comes back.
  * HTTP 429 is retried with a linear backoff (attempt * 4 seconds).
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
import urllib.request
from typing import Any

OVER_API_BASE = "https://www.over.org.il/api/knesset-db/sql"
SSL_CTX = ssl._create_unverified_context()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


logger = logging.getLogger("over_api")


class OverApiError(RuntimeError):
    """A request to the Over API failed after exhausting its retries.

    Distinct from a successful response carrying no rows. Callers page until a
    request comes back empty, so a failure that returned `{"rows": []}` instead
    of raising would be indistinguishable from the end of the data — the caller
    would treat a truncated fetch, or a total outage, as a complete result.
    """


def run_over_sql(sql: str, retries: int = 10) -> dict[str, Any]:
    """Execute SQL query against Over Knesset API with retry handling.

    Raises `OverApiError` if the request cannot be completed.
    """
    data = json.dumps({"sql": sql}).encode("utf-8")
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(OVER_API_BASE, data=data, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                offset_match = re.search(r"OFFSET\s+(\d+)", sql, re.IGNORECASE)
                offset_str = f"OFFSET {offset_match.group(1)}" if offset_match else "OFFSET 0"
                if attempt < retries:
                    sleep_time = attempt * 4
                    logger.warning(f"Rate limited (429) at {offset_str}. Retrying in {sleep_time}s (attempt {attempt}/{retries})...")
                    time.sleep(sleep_time)
                    continue
                raise OverApiError(
                    f"rate limited (429) at {offset_str} after {retries} attempts"
                ) from e
            raise OverApiError(
                f"HTTP {e.code} from the Over API for query: {sql[:100].strip()}"
            ) from e
        except OverApiError:
            raise
        except Exception as e:
            if attempt < retries:
                time.sleep(attempt)
                continue
            raise OverApiError(
                f"could not reach the Over API after {retries} attempts: {e}"
            ) from e
    raise OverApiError(f"exhausted {retries} attempts for query: {sql[:100].strip()}")

def fetch_all_over_sql(
    sql: str,
    user_limit: int | None = None,
    page_size: int = 1000,
    progress_label: str | None = None,
) -> list[dict[str, Any]]:
    """Execute SQL query against Over API with automatic offset pagination if rows hit page cap."""
    clean_sql = sql.strip().rstrip(";").strip()
    if user_limit and user_limit <= page_size:
        res = run_over_sql(f"{clean_sql} LIMIT {user_limit};")
        return res.get("rows", [])

    all_rows = []
    offset = 0

    while True:
        current_limit = page_size
        if user_limit and (len(all_rows) + current_limit) > user_limit:
            current_limit = user_limit - len(all_rows)

        page_sql = f"{clean_sql} LIMIT {current_limit} OFFSET {offset};"
        res = run_over_sql(page_sql)
        rows = res.get("rows", [])
        if not rows:
            break
        all_rows.extend(rows)
        if progress_label:
            logger.info(
                "%s: fetched %s rows", progress_label, f"{len(all_rows):,}"
            )
        if len(rows) < current_limit or (user_limit and len(all_rows) >= user_limit):
            break
        offset += len(rows)
        time.sleep(0.3)  # Brief delay to prevent rate-limiting during page iteration

    return all_rows

def map_vote_result(result_code: int | None, result_desc: str | None) -> str:
    """Map Over API vote result codes/descriptions to DB schema enum ('for'|'against'|'abstain'|'absent')."""
    if result_code == 7 or result_desc == "בעד":
        return "for"
    elif result_code == 8 or result_desc == "נגד":
        return "against"
    elif result_code in (6, 9) or result_desc in ("נמנע", "נוכח"):
        return "abstain"
    elif result_code == 10 or result_desc in ("לא נכח/ אינו נוכח", "לא נכח"):
        return "absent"
    return "absent"

def fetch_over_vote_events(
    limit: int | None = None, start_date: str | None = None
) -> list[dict[str, Any]]:
    """
    Fetch vote events from Over API kns_plenumvote joined with kns_documentbill filepath for draft documents.
    
    NOTE ON DISTINCT ON (v.id):
    Joining kns_plenumvote (via v.itemid = d.billid) with kns_documentbill produces duplicate rows
    per vote event because a single bill often has multiple draft document files attached
    (e.g., .pdf, .docx, and .tif versions of the same draft).
    Using 'DISTINCT ON (v.id)' ensures PostgreSQL returns exactly 1 unique vote event row,
    selecting the newest primary draft document (ORDER BY v.id DESC, d.lastupdateddate DESC).

    NOTE ON v.itemid:
    kns_plenumvote.itemid is the Knesset bill id the vote event belongs to (the same key
    kns_documentbill.billid and kns_bill.id use). It is selected as `knesset_bill_id` so the
    vote_event MERGE can resolve `vote_event.bill_id` from `bill.knesset_bill_id`.
    """
    logger.info(f"Fetching vote events from Over API (limit={limit}, start_date={start_date})...")
    date_clause = f" AND v.votedatetime >= '{start_date}'" if start_date else ""
    sql = f"""
    SELECT DISTINCT ON (v.id)
        v.id, 
        v.votedatetime, 
        v.votetitle, 
        v.votesubject,
        v.itemid AS knesset_bill_id,
        d.filepath AS draft_document_uri
    FROM kns_plenumvote v
    LEFT JOIN kns_documentbill d ON v.itemid = d.billid AND d.grouptypeid IN (1, 2, 4)
    WHERE v.id IS NOT NULL{date_clause}
    ORDER BY v.id DESC, d.lastupdateddate DESC;
    """
    return fetch_all_over_sql(sql, user_limit=limit)

def fetch_over_vote_results(
    vote_mkids: list[int],
    limit: int | None = None,
    start_date: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch vote results for reviewed source IDs without joining kns_person."""
    if not vote_mkids:
        raise ValueError("vote_mkids cannot be empty")
    logger.info(
        "Fetching MK vote results from Over API "
        f"(reviewed_mkids={len(vote_mkids)}, limit={limit}, start_date={start_date})..."
    )
    date_join = " JOIN kns_plenumvote v ON vr.voteid = v.id" if start_date else ""
    date_clause = f" AND v.votedatetime >= '{start_date}'" if start_date else ""
    source_ids = ",".join(str(int(mkid)) for mkid in sorted(set(vote_mkids)))
    sql = f"""
    SELECT vr.mkid, vr.voteid, vr.resultcode, vr.resultdesc
    FROM kns_plenumvoteresult vr{date_join}
    WHERE vr.mkid IN ({source_ids})
      AND vr.voteid IS NOT NULL{date_clause}
    ORDER BY vr.id DESC;
    """
    return fetch_all_over_sql(
        sql,
        user_limit=limit,
        progress_label="Vote-result download",
    )
