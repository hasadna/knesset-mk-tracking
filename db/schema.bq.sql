-- ============================================================================
-- mk-tracking — BigQuery schema v4 (GCP hackathon infra) — DEPLOYED
-- ============================================================================
-- v3 spec change (see docs/DATABASE_DESIGN.md §10):
--  * Position layer removed: no quote extraction, no structured stance/valence.
--    Posts are tagged with issues (post_issue); an SLM writes a free-text
--    per-(MK, issue) opinion summary (mk_issue_summary).
--  * Votes generalized: vote_event covers plenum readings AND committee
--    motions; mk_vote references events. Issue mapping exists at both bill
--    level (bill_issue) and event level (vote_event_issue), with provenance.
-- v4 spec change (see docs/DATABASE_DESIGN.md §11):
--  * Issue matching has separate social-post and bill prompts.
--  * Summaries carry quality, limitations, and normalized supporting posts.
--  * Bills may reference an enacted-law document; vote events may reference
--    the draft document that was put to a vote.
--
-- Apply with:
--   bq query --use_legacy_sql=false --project_id="$GCP_PROJECT" < db/schema.bq.sql
-- Idempotent once a v4 dataset exists (IF NOT EXISTS / OR REPLACE).
-- Upgrading an existing v3 dataset requires a rebuild because BigQuery's
-- CREATE TABLE IF NOT EXISTS does not reshape existing tables.
--
-- Conventions (unchanged from v2): STRING UUID ids; PK/FK NOT ENFORCED;
-- allowed values in column descriptions (pipeline validates); idempotency via
-- MERGE on natural keys; "current" == end_date IS NULL.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS mk_tracking OPTIONS (location = 'US');

-- ---------------------------------------------------------------------------
-- Reference / dimension tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mk_tracking.party (
  id         STRING DEFAULT GENERATE_UUID() NOT NULL,
  name_he    STRING NOT NULL,
  name_en    STRING,
  short_name STRING,
  is_current BOOL DEFAULT TRUE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED
);

CREATE TABLE IF NOT EXISTS mk_tracking.committee (
  id                   STRING DEFAULT GENERATE_UUID() NOT NULL,
  knesset_committee_id INT64,               -- external Open Knesset / OData id (natural key)
  name_he              STRING NOT NULL,
  name_en              STRING,
  PRIMARY KEY (id) NOT ENFORCED
);

-- Curated issue taxonomy sourced from the mk_work prototype and maintained in
-- db/seed.bq.sql.
CREATE TABLE IF NOT EXISTS mk_tracking.issue (
  id                                STRING DEFAULT GENERATE_UUID() NOT NULL,
  slug                              STRING NOT NULL, -- stable machine key (natural key)
  name                              STRING NOT NULL,
  description                       STRING,
  prompt_for_social_post_similarity STRING NOT NULL,
  prompt_for_bill_similarity        STRING NOT NULL,
  rating_scale                      JSON NOT NULL, -- keys 1..5; first axis pole=1, second=5
  sort_order                        INT64 DEFAULT 0 NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED
);

-- Eight multilingual semantic anchors per issue (4 Hebrew, 2 English,
-- 2 Arabic). Natural key (issue_id, anchor_index). These rows are curated and
-- embedded once; the scoring pipeline reads them but never regenerates them.
CREATE TABLE IF NOT EXISTS mk_tracking.issue_anchor (
  id              STRING DEFAULT GENERATE_UUID() NOT NULL,
  issue_id        STRING NOT NULL,
  anchor_index    INT64 NOT NULL OPTIONS (description = 'one of: 1..8'),
  language        STRING NOT NULL OPTIONS (description = 'one of: he|en|ar'),
  text            STRING NOT NULL,
  embedding       ARRAY<FLOAT64>,
  embedding_model STRING NOT NULL,
  created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (issue_id) REFERENCES mk_tracking.issue (id) NOT ENFORCED
)
CLUSTER BY issue_id;

-- ---------------------------------------------------------------------------
-- MK, affiliations, roles, accounts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mk_tracking.mk (
  id                STRING DEFAULT GENERATE_UUID() NOT NULL,
  knesset_member_id INT64,                  -- external hook (natural key)
  slug              STRING NOT NULL,        -- URL key, e.g. 'yair-lapid'
  full_name_he      STRING NOT NULL,
  full_name_en      STRING,
  photo_url         STRING,
  birth_date        DATE,
  gender            STRING,
  home_city         STRING,
  bio_he            STRING,
  bio_en            STRING,
  is_current        BOOL DEFAULT TRUE NOT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED
);

CREATE TABLE IF NOT EXISTS mk_tracking.mk_affiliation (
  id         STRING DEFAULT GENERATE_UUID() NOT NULL,
  mk_id      STRING NOT NULL,
  party_id   STRING NOT NULL,
  start_date DATE,
  end_date   DATE,                          -- NULL == current
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (mk_id) REFERENCES mk_tracking.mk (id) NOT ENFORCED,
  FOREIGN KEY (party_id) REFERENCES mk_tracking.party (id) NOT ENFORCED
);

CREATE TABLE IF NOT EXISTS mk_tracking.mk_role (
  id           STRING DEFAULT GENERATE_UUID() NOT NULL,
  mk_id        STRING NOT NULL,
  role_type    STRING NOT NULL OPTIONS (description = 'one of: prime_minister|minister|deputy_minister|knesset_speaker|deputy_speaker|committee_chair|committee_member|faction_chair|coalition|opposition|other'),
  title_he     STRING,
  title_en     STRING,
  committee_id STRING,
  start_date   DATE,
  end_date     DATE,                        -- NULL == current
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (mk_id) REFERENCES mk_tracking.mk (id) NOT ENFORCED
);

CREATE TABLE IF NOT EXISTS mk_tracking.mk_social_account (
  id        STRING DEFAULT GENERATE_UUID() NOT NULL,
  mk_id     STRING NOT NULL,
  platform  STRING NOT NULL OPTIONS (description = 'one of: twitter|facebook|instagram|telegram|tiktok|gov_il|other'),
  handle    STRING,
  url       STRING,
  is_active BOOL DEFAULT TRUE NOT NULL,
  verified  BOOL DEFAULT FALSE NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (mk_id) REFERENCES mk_tracking.mk (id) NOT ENFORCED
);

-- ---------------------------------------------------------------------------
-- Social posts + issue tagging (v3: tagging is at POST level; no quote layer)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mk_tracking.social_post (
  id               STRING DEFAULT GENERATE_UUID() NOT NULL,
  mk_id            STRING NOT NULL,
  account_id       STRING,
  platform         STRING NOT NULL OPTIONS (description = 'one of: twitter|facebook|instagram|telegram|tiktok|gov_il|other'),
  platform_post_id STRING,                  -- natural key with platform
  url              STRING,
  posted_at        TIMESTAMP,
  text             STRING,
  embedding        ARRAY<FLOAT64>,          -- empty or exactly 3072 values; pipeline-validated
  language         STRING OPTIONS (description = 'one of: he|en|ar|other'),
  engagement       JSON,
  is_deleted       BOOL DEFAULT FALSE NOT NULL,
  fetched_at       TIMESTAMP,
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED
)
PARTITION BY DATE(posted_at)
CLUSTER BY mk_id, platform;

-- A post can touch several issues. Natural key (post_id, issue_id), via MERGE.
CREATE TABLE IF NOT EXISTS mk_tracking.post_issue (
  id                  STRING DEFAULT GENERATE_UUID() NOT NULL,
  post_id             STRING NOT NULL,
  issue_id            STRING NOT NULL,
  confidence          NUMERIC OPTIONS (description = '0.000..1.000 — tagging confidence; pipeline-validated'),
  is_concrete_promise BOOL DEFAULT FALSE NOT NULL OPTIONS (description = 'post contains a concrete policy promise w.r.t. this issue (retained from v2 spec — pending team confirmation)'),
  model_version       STRING,
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (post_id) REFERENCES mk_tracking.social_post (id) NOT ENFORCED,
  FOREIGN KEY (issue_id) REFERENCES mk_tracking.issue (id) NOT ENFORCED
)
CLUSTER BY issue_id;

-- Gemini-classified semantic clusters used as a negative gate for issue scoring.
-- Natural key (model_version, cluster_id). The centroid is the raw K-means
-- center in the normalized tweet-embedding space.
CREATE TABLE IF NOT EXISTS mk_tracking.tweet_cluster (
  model_version   STRING NOT NULL,
  cluster_id      INT64 NOT NULL,
  centroid        ARRAY<FLOAT64>,
  title_he        STRING NOT NULL,
  description_he  STRING NOT NULL,
  cluster_size    INT64 NOT NULL,
  is_garbage      BOOL DEFAULT FALSE NOT NULL,
  embedding_model STRING NOT NULL,
  summary_model   STRING NOT NULL,
  k               INT64 NOT NULL,
  random_state    INT64 NOT NULL,
  n_init          INT64 NOT NULL,
  updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  PRIMARY KEY (model_version, cluster_id) NOT ENFORCED
)
CLUSTER BY model_version, cluster_id;

-- ---------------------------------------------------------------------------
-- Per-(MK, issue) opinion summary: evidence-grounded text plus a 1..5 rating
-- defined by issue.rating_scale. A flip-flop observation remains in the text.
-- Natural key (mk_id, issue_id); overwrite semantics via MERGE.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mk_tracking.mk_issue_summary (
  id            STRING DEFAULT GENERATE_UUID() NOT NULL,
  mk_id         STRING NOT NULL,
  issue_id      STRING NOT NULL,
  summary_he    STRING,
  extended_summary_he STRING,
  quality       STRING NOT NULL OPTIONS (description = 'one of: strong|partial|none'),
  rating        INT64 OPTIONS (description = '1..5 on issue.rating_scale; NULL when quality=none'),
  limitations   STRING,
  model_version STRING,
  generation_run_id STRING,
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (mk_id) REFERENCES mk_tracking.mk (id) NOT ENFORCED,
  FOREIGN KEY (issue_id) REFERENCES mk_tracking.issue (id) NOT ENFORCED
)
CLUSTER BY mk_id;

-- One row per attempted MK summary generation. elapsed_ms records model
-- generation time; publication errors are retained for resumable operations.
CREATE TABLE IF NOT EXISTS mk_tracking.summary_generation_run (
  id            STRING NOT NULL,
  mk_id         STRING NOT NULL,
  model_version STRING NOT NULL,
  started_at    TIMESTAMP NOT NULL,
  completed_at  TIMESTAMP,
  elapsed_ms    INT64,
  status        STRING NOT NULL OPTIONS (description = 'one of: running|completed|failed'),
  issue_count   INT64,
  post_count    INT64,
  error_message STRING,
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (mk_id) REFERENCES mk_tracking.mk (id) NOT ENFORCED
)
CLUSTER BY mk_id;

-- Evidence supporting a generated summary. A summary may cite several posts,
-- and a post may support several summaries. Natural key (summary_id, post_id).
CREATE TABLE IF NOT EXISTS mk_tracking.mk_issue_summary_supporting_post (
  summary_id STRING NOT NULL,
  post_id    STRING NOT NULL,
  FOREIGN KEY (summary_id) REFERENCES mk_tracking.mk_issue_summary (id) NOT ENFORCED,
  FOREIGN KEY (post_id) REFERENCES mk_tracking.social_post (id) NOT ENFORCED
)
CLUSTER BY summary_id;

-- ---------------------------------------------------------------------------
-- Parliamentary activity (v3: generalized vote events — plenum + committee)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mk_tracking.bill (
  id               STRING DEFAULT GENERATE_UUID() NOT NULL,
  knesset_bill_id  INT64,                    -- natural key from Open Knesset / OData
  title_he         STRING NOT NULL,
  title_en         STRING,
  summary          STRING,
  embedding        ARRAY<FLOAT64>,          -- empty or exactly 3072 values; loader-validated
  enacted_law_document_uri STRING,
  status           STRING,
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP() NOT NULL,
  PRIMARY KEY (id) NOT ENFORCED
);

-- Anything MKs vote on: a plenum reading of a bill, or a committee motion.
-- Natural key: external_key (stable string from the source, e.g.
-- 'odata:vote:12345' or 'committee:450:2026-03-02:item4').
CREATE TABLE IF NOT EXISTS mk_tracking.vote_event (
  id           STRING DEFAULT GENERATE_UUID() NOT NULL,
  external_key STRING NOT NULL,             -- natural key for MERGE
  event_kind   STRING NOT NULL OPTIONS (description = 'one of: plenum|committee'),
  bill_id      STRING,                      -- set for plenum readings (and committee votes on bills)
  committee_id STRING,                      -- set for committee motions
  reading      INT64,                       -- 1/2/3 for plenum bill readings; NULL otherwise
  title_he     STRING NOT NULL,
  title_en     STRING,
  draft_document_uri STRING,               -- draft/text presented for this vote
  voted_at     TIMESTAMP,
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (bill_id) REFERENCES mk_tracking.bill (id) NOT ENFORCED,
  FOREIGN KEY (committee_id) REFERENCES mk_tracking.committee (id) NOT ENFORCED
);

-- Issue mapping, two levels, both M:N-capable (supports 1:N, N:1, M:N):
--  * bill_issue: bill-level — one mapping covers all of that bill's vote events
--  * vote_event_issue: event-level — for committee motions and for events whose
--    subject diverges from the parent bill ("subtleties not apparent from the title")
-- Effective mapping for an event = vote_event_issue rows ∪ bill_issue rows of its bill.
-- Both carry provenance: the mapping is complex and judgment-laden — record how
-- it was made and why.
CREATE TABLE IF NOT EXISTS mk_tracking.bill_issue (
  bill_id        STRING NOT NULL,
  issue_id       STRING NOT NULL,
  mapping_method STRING OPTIONS (description = 'one of: manual|model|rule'),
  mapping_note   STRING,                    -- the non-obvious rationale, if any
  confidence     NUMERIC,
  FOREIGN KEY (bill_id) REFERENCES mk_tracking.bill (id) NOT ENFORCED,
  FOREIGN KEY (issue_id) REFERENCES mk_tracking.issue (id) NOT ENFORCED
);

CREATE TABLE IF NOT EXISTS mk_tracking.vote_event_issue (
  vote_event_id  STRING NOT NULL,
  issue_id       STRING NOT NULL,
  mapping_method STRING OPTIONS (description = 'one of: manual|model|rule'),
  mapping_note   STRING,
  confidence     NUMERIC,
  FOREIGN KEY (vote_event_id) REFERENCES mk_tracking.vote_event (id) NOT ENFORCED,
  FOREIGN KEY (issue_id) REFERENCES mk_tracking.issue (id) NOT ENFORCED
);

-- Bill authorship: which MKs initiated/co-sponsored a bill. Natural key
-- (bill_id, mk_id). Independent of votes — a bill has authors even if never
-- voted on; maps 1:1 to Knesset OData KNS_BillInitiator.
CREATE TABLE IF NOT EXISTS mk_tracking.bill_author (
  bill_id STRING NOT NULL,
  mk_id   STRING NOT NULL,
  role    STRING OPTIONS (description = 'one of: initiator|co_initiator'),
  FOREIGN KEY (bill_id) REFERENCES mk_tracking.bill (id) NOT ENFORCED,
  FOREIGN KEY (mk_id) REFERENCES mk_tracking.mk (id) NOT ENFORCED
);

-- One row per MK per vote event. Natural key (mk_id, vote_event_id).
CREATE TABLE IF NOT EXISTS mk_tracking.mk_vote (
  id            STRING DEFAULT GENERATE_UUID() NOT NULL,
  mk_id         STRING NOT NULL,
  vote_event_id STRING NOT NULL,
  vote          STRING NOT NULL OPTIONS (description = 'one of: for|against|abstain|absent'),
  PRIMARY KEY (id) NOT ENFORCED,
  FOREIGN KEY (mk_id) REFERENCES mk_tracking.mk (id) NOT ENFORCED,
  FOREIGN KEY (vote_event_id) REFERENCES mk_tracking.vote_event (id) NOT ENFORCED
)
CLUSTER BY mk_id;

-- ---------------------------------------------------------------------------
-- Connections between MKs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mk_tracking.mk_relation (
  id            STRING DEFAULT GENERATE_UUID() NOT NULL,
  mk_id         STRING NOT NULL,
  related_mk_id STRING NOT NULL,
  relation_type STRING NOT NULL OPTIONS (description = 'one of: same_party|similar_positions|notable|ally|rival'),
  issue_id      STRING,
  score         NUMERIC,
  PRIMARY KEY (id) NOT ENFORCED
);

-- ---------------------------------------------------------------------------
-- Views (read models)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW mk_tracking.v_mk_card AS
SELECT
  m.id, m.slug, m.full_name_he, m.full_name_en, m.photo_url, m.is_current,
  p.name_he AS party_he,
  p.name_en AS party_en,
  ARRAY(SELECT DISTINCT sa.platform FROM mk_tracking.mk_social_account sa
         WHERE sa.mk_id = m.id AND sa.is_active)                 AS platforms,
  ARRAY(SELECT DISTINCT r.role_type FROM mk_tracking.mk_role r
         WHERE r.mk_id = m.id AND r.end_date IS NULL)            AS current_roles,
  (SELECT COUNT(DISTINCT s.issue_id) FROM mk_tracking.mk_issue_summary s
    WHERE s.mk_id = m.id)                                        AS issue_count
FROM mk_tracking.mk m
LEFT JOIN mk_tracking.mk_affiliation a ON a.mk_id = m.id AND a.end_date IS NULL
LEFT JOIN mk_tracking.party p ON p.id = a.party_id;

-- The profile's per-issue blocks: SLM summary + post/promise counts.
CREATE OR REPLACE VIEW mk_tracking.v_mk_issue_summary AS
SELECT
  s.id AS summary_id,
  s.mk_id, s.issue_id, i.slug AS issue_slug, i.name AS issue_name,
  i.description AS issue_description,
  s.summary_he, s.extended_summary_he, s.quality, s.rating, s.limitations,
  s.model_version, s.generation_run_id, s.updated_at,
  ARRAY(
    SELECT link.post_id
    FROM mk_tracking.mk_issue_summary_supporting_post link
    WHERE link.summary_id = s.id
  ) AS supporting_post_ids,
  (SELECT COUNT(*) FROM mk_tracking.post_issue pi
     JOIN mk_tracking.social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id AND pi.issue_id = s.issue_id)       AS post_count,
  (SELECT COUNT(*) FROM mk_tracking.post_issue pi
     JOIN mk_tracking.social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id AND pi.issue_id = s.issue_id
      AND pi.is_concrete_promise)                                AS promise_count
FROM mk_tracking.mk_issue_summary s
JOIN mk_tracking.issue i ON i.id = s.issue_id;

-- "Matrix transpose": for one issue, every MK's summary + evidence volume.
CREATE OR REPLACE VIEW mk_tracking.v_issue_landscape AS
SELECT
  i.id AS issue_id, i.slug AS issue_slug, i.name AS issue_name,
  s.mk_id, m.full_name_he, m.slug AS mk_slug,
  s.summary_he, s.extended_summary_he, s.quality, s.rating, s.limitations, s.updated_at,
  (SELECT COUNT(*) FROM mk_tracking.post_issue pi
     JOIN mk_tracking.social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id AND pi.issue_id = i.id)             AS post_count
FROM mk_tracking.issue i
JOIN mk_tracking.mk_issue_summary s ON s.issue_id = i.id
JOIN mk_tracking.mk m ON m.id = s.mk_id;

-- Effective vote_event -> issue mapping (event-level ∪ bill-level).
CREATE OR REPLACE VIEW mk_tracking.v_vote_event_issues AS
SELECT vei.vote_event_id, vei.issue_id FROM mk_tracking.vote_event_issue vei
UNION DISTINCT
SELECT e.id AS vote_event_id, bi.issue_id
FROM mk_tracking.vote_event e
JOIN mk_tracking.bill_issue bi ON bi.bill_id = e.bill_id;

-- Authorship per (MK, issue): bills the MK initiated/co-sponsored, rolled up
-- to the issue taxonomy via bill_issue. A strong "did" signal alongside votes.
CREATE OR REPLACE VIEW mk_tracking.v_mk_issue_authorship AS
SELECT
  ba.mk_id, bi.issue_id,
  COUNTIF(ba.role = 'initiator')    AS bills_initiated,
  COUNTIF(ba.role = 'co_initiator') AS bills_co_initiated,
  COUNT(*)                          AS bills_authored_total
FROM mk_tracking.bill_author ba
JOIN mk_tracking.bill_issue bi ON bi.bill_id = ba.bill_id
GROUP BY ba.mk_id, bi.issue_id;

-- "Said vs did": per (MK, issue, arena) vote tallies, to sit next to the
-- SLM summary in the UI. Arena split lets the UI show plenum and committee
-- records separately.
CREATE OR REPLACE VIEW mk_tracking.v_said_vs_did AS
SELECT
  v.mk_id, m.issue_id, e.event_kind,
  COUNTIF(v.vote = 'for')     AS votes_for,
  COUNTIF(v.vote = 'against') AS votes_against,
  COUNTIF(v.vote = 'abstain') AS votes_abstain,
  COUNTIF(v.vote = 'absent')  AS votes_absent
FROM mk_tracking.mk_vote v
JOIN mk_tracking.vote_event e ON e.id = v.vote_event_id
JOIN mk_tracking.v_vote_event_issues m ON m.vote_event_id = e.id
GROUP BY v.mk_id, m.issue_id, e.event_kind;
