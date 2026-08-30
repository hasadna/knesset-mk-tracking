# 🏛️ MK Tracking: Project Status & Parliamentary Data Pipeline Design

> **Overview:** A shareable technical breakdown of the **MK Tracking** project — an AI-powered system that tracks Israeli Members of Knesset (MKs) by comparing what they say on social media against what they actually do in parliament (votes & bills).

---

## 1. Executive Summary & Project Status

### What We Are Building
A platform that maps MK activity across **7 core policy issues** (e.g., Cost of Living, Judicial System, Conscription/Haredi Draft, Defense). It solves a fundamental problem in Israeli politics: public representatives rarely publish clear manifestos, making it hard to track their real positions and consistency over time.

### Current System Stack & Deployed Infrastructure
* **Database & Infra:** Deployed on **Google Cloud BigQuery** (`mk_tracking` dataset, v4 schema).
* **Social Media Pipeline:** Scrapes tweets, generates vectors via `gemini-embedding-2` (3,072 dimensions), clusters posts with K-means ($K=30$) to filter out non-political noise, and scores tweets against the issue taxonomy.
* **Knesset Data Ingestion:** Ingests MKs, parties, affiliations, bills (`bill`), vote events (`vote_event`), and individual votes (`mk_vote`) from the Open Knesset / Over API.
* **Opinion Summary Pipeline:** Uses Gemini to synthesize evidence-grounded stance summaries per (MK, issue).
* **Frontend & API:** React 19 + Vite + TypeScript frontend backed by a FastAPI web server reading live BigQuery data.

---

## 2. "What They Said" vs. "What They Did"

An essential architectural choice in the system is separating **Social Rhetoric** from **Parliamentary Record**:

```
                              ┌─────────────────────────────────────────┐
                              │            MK Profile / UI              │
                              └────────────────────┬────────────────────┘
                                                   │
                   ┌───────────────────────────────┴───────────────────────────────┐
                   ▼                                                               ▼
    ┌─────────────────────────────┐                                ┌─────────────────────────────┐
    │     "WHAT THEY SAID"        │                                │      "WHAT THEY DID"        │
    │  Social Media Stance (X)    │                                │  Parliamentary Votes & Bills│
    └──────────────┬──────────────┘                                └──────────────┬──────────────┘
                   │                                                              │
                   ▼                                                              ▼
 ┌───────────────────────────────────┐                          ┌───────────────────────────────────┐
 │ • Stance summary (mk_issue_summary│                          │ • Materialized voting record      │
 │ • Grounded ONLY in tweets        │                          │   (tbl_mk_issue_bill_votes)       │
 │ • Strict LLM prompt boundary      │                          │ • Bill authorship (bill_author)   │
 │ • Supporting tweet citations     │                          │ • Direct links to Knesset portal  │
 └───────────────────────────────────┘                          └───────────────────────────────────┘
```

> **Are bills included in the text opinion summaries?**  
> **No.** The opinion summaries in `mk_issue_summary` are derived **exclusively from Twitter/X posts**. The system prompt explicitly enforces:  
> *"You analyze political evidence in Hebrew. You may infer ONLY from the provided tweets."*  
> 
> Bills and votes are maintained separately in SQL views like `v_said_vs_did` and `v_mk_issue_authorship` so the UI can display actual voting behavior directly alongside social media statements.

---

## 3. End-to-End Pipeline Plan: Integrating MK Votes & Bills

To bring legislative bills, voting history, and official law links into the UI per issue, we designed a 4-stage data pipeline.

### Stage 1: Ingestion & Link Resolution

1. **Bill Authorship (`bill_author`):**
   * Ingests `kns_billinitiator` from the Open Knesset API.
   * Connects MKs to bills they authored, setting `role` to `initiator` (primary sponsor) or `co_initiator`.
2. **Canonical External Links:**
   * **Official Knesset Portal URL:** Pre-computed and saved in `bill.knesset_portal_url`:
     `https://main.knesset.gov.il/Activity/Legislation/Laws/Pages/LawBill.aspx?t=lawsuggestionssearch&lawitemid={knesset_bill_id}`
   * **Open Knesset URL:** `https://oknesset.org/bill/{knesset_bill_id}/`
   * **Enacted Law Document PDF:** Served via a backend proxy `/api/documents/bills/{bill_id}` that streams or provides signed download links for the GCS PDF (`enacted_law_document_uri`).

---

### Stage 2: Targeted LLM Semantic Classification (`bill_issue`)

To avoid wastefully processing all 23,240 scraped bills through an LLM, the pipeline targets only bills that saw floor or committee votes in active/recent Knessets:

```sql
SELECT DISTINCT 
    b.id, 
    b.knesset_bill_id, 
    b.title_he, 
    COALESCE(b.summary, b.title_he) AS context_text
FROM mk_tracking.bill b
JOIN mk_tracking.vote_event ve ON ve.bill_id = b.id
JOIN mk_tracking.mk_vote mkv ON mkv.vote_event_id = ve.id
WHERE ve.voted_at >= TIMESTAMP('2015-01-01 00:00:00 UTC')
  AND NOT EXISTS (
      SELECT 1 FROM mk_tracking.bill_issue bi WHERE bi.bill_id = b.id
  );
```

* **LLM Engine:** `gemini-3.6-flash` structured evaluations.
* **Confidence Gate:** Filters out noisy matches where `confidence < 0.5`.
* **Taxonomy Guard:** Validates returned `issue_id`s against `mk_tracking.issue.slug` to reject hallucinations.
* **Cost:** ~$1.50 for 5,000 active bills.

---

### Stage 3: Read Model Materialization & BigQuery Views

We construct a robust read model (`v_mk_issue_bill_votes`) that handles edge cases like committee motions without attached bills, multi-reading timelines, and large string aggregation:

```sql
CREATE OR REPLACE VIEW mk_tracking.v_mk_issue_bill_votes AS
SELECT 
    m.slug AS mk_slug,
    i.slug AS issue_slug,
    COALESCE(b.id, ve.committee_id, ve.id) AS context_id,
    ANY_VALUE(b.knesset_bill_id) AS knesset_bill_id,
    ANY_VALUE(COALESCE(b.title_he, ve.title_he)) AS title,
    ANY_VALUE(b.summary) AS summary,
    ANY_VALUE(b.knesset_portal_url) AS knesset_portal_url,
    ANY_VALUE(ba.role) AS authorship_role,
    ARRAY_AGG(STRUCT(
        ve.id AS vote_event_id,
        ve.title_he AS event_title,
        ve.event_kind,
        ve.reading,
        ve.voted_at,
        mkv.vote
    ) ORDER BY ve.voted_at DESC) AS voting_events
FROM mk_tracking.mk m
JOIN mk_tracking.mk_vote mkv ON mkv.mk_id = m.id
JOIN mk_tracking.vote_event ve ON ve.id = mkv.vote_event_id
JOIN mk_tracking.v_vote_event_issues vei ON vei.vote_event_id = ve.id
JOIN mk_tracking.issue i ON i.id = vei.issue_id
LEFT JOIN mk_tracking.bill b ON b.id = ve.bill_id
LEFT JOIN mk_tracking.bill_author ba ON ba.bill_id = b.id AND ba.mk_id = m.id
GROUP BY m.slug, i.slug, context_id;
```

> **Performance Optimization:** To eliminate 1–3s BigQuery compilation delays and scan costs on page clicks, the view is materialized into a clustered table (`tbl_mk_issue_bill_votes` clustered by `mk_slug, issue_slug`), allowing FastAPI to serve queries in under **100ms**.

---

### Stage 4: FastAPI Data Contract

The API endpoint consumes the materialized table and returns structured JSON for the frontend:

`GET /api/mks/{mk_slug}/issues/{issue_slug}/votes`

```json
{
  "mk_slug": "yair-lapid",
  "issue_slug": "cost-of-living",
  "items": [
    {
      "context_id": "bill-uuid-1234",
      "knesset_bill_id": 56789,
      "title": "חוק פיקוח על מחירי המזון",
      "summary": "הצעת חוק לקביעת מחירי מרבי למאכלי יסוד...",
      "authorship_role": "initiator",
      "links": {
        "knesset_portal": "https://main.knesset.gov.il/Activity/Legislation/Laws/Pages/LawBill.aspx?t=lawsuggestionssearch&lawitemid=56789",
        "open_knesset": "https://oknesset.org/bill/56789/",
        "document_proxy_url": "/api/documents/bills/bill-uuid-1234"
      },
      "voting_timeline": [
        {
          "vote_event_id": "ve-333",
          "event_title": "הצבעה בקריאה שלישית",
          "event_kind": "plenum",
          "reading": 3,
          "voted_at": "2025-11-12T14:00:00Z",
          "vote": "for"
        },
        {
          "vote_event_id": "ve-111",
          "event_title": "הצבעה בקריאה ראשונה",
          "event_kind": "plenum",
          "reading": 1,
          "voted_at": "2025-06-01T10:00:00Z",
          "vote": "for"
        }
      ]
    }
  ]
}
```

---

## 4. Key Pitfalls & Mitigations Matrix

| Identified Pitfall | Root Cause / Vulnerability | Architectural Mitigation |
| :--- | :--- | :--- |
| **Missing `bill_issue` rows** | `src/mk_tracking/process/bill_issues/cli.py` hasn't been run against the full BQ dataset. | Run prioritized backfill for bills with votes since 2015 before deploying API endpoint. |
| **Dropping Committee Motions** | `INNER JOIN bill` on `vote_event` excludes non-bill votes. | Use `LEFT JOIN bill` in `v_mk_issue_bill_votes` and fall back to `ve.title_he`. |
| **BigQuery UI Latency & Cost** | Executing complex JOINs and `ARRAY_AGG` dynamically on page loads. | Materialize `v_mk_issue_bill_votes` into clustered table `tbl_mk_issue_bill_votes`. |
| **Unresolvable `gs://` URIs** | Browsers cannot directly render Google Cloud Storage paths. | FastAPI document proxy route `/api/documents/bills/{id}` generating signed download links. |
| **Duplicate Votes per Issue** | `bill_issue` and `vote_event_issue` both mapping the same issue. | `v_vote_event_issues` view performs `UNION DISTINCT` prior to joining votes. |
| **LLM Hallucinations** | Gemini returning invalid issue slugs. | Python pipeline enforces strict set validation against `mk_tracking.issue.slug`. |
| **Null Bill Summaries** | Bills with `summary IS NULL` breaking LLM prompts. | Query uses `COALESCE(b.summary, b.title_he)` as context text. |
