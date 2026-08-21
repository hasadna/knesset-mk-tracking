-- ============================================================================
-- mk-tracking — database schema (PostgreSQL) — v6
-- ============================================================================
-- Apply with:  psql "$DATABASE_URL" -f db/schema.sql
-- Requires PostgreSQL 15+ (gen_random_uuid is built in from 13; security_invoker
-- views from 15).
--
-- Derived from db/schema.bq.sql (the BigQuery v3-v6 schema) and validated
-- against the live SQLite export (mk_tracking.db, 2026-08-21). Where the export
-- showed the data cannot yet satisfy a constraint, the constraint is declared
-- NOT VALID rather than dropped: new writes are checked, legacy rows are not,
-- and `ALTER TABLE ... VALIDATE CONSTRAINT ...` closes it once cleaned.
-- See db/DATA_QUALITY.md for the measured findings.
--
-- The v2 design reference this file replaces is kept at db/schema.v2.reference.sql.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS mk_tracking;
SET search_path TO mk_tracking, public;

CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- substring search over Hebrew text

-- ---------------------------------------------------------------------------
-- Enum types — closed value sets, verified against the export
-- ---------------------------------------------------------------------------
CREATE TYPE platform        AS ENUM ('twitter','facebook','instagram','telegram','tiktok','gov_il','other');
CREATE TYPE content_lang    AS ENUM ('he','en','ar','other');
CREATE TYPE anchor_lang     AS ENUM ('he','en','ar');
CREATE TYPE summary_quality AS ENUM ('strong','partial','none');
CREATE TYPE vote_value      AS ENUM ('for','against','abstain','absent');
CREATE TYPE mapping_method  AS ENUM ('manual','model','rule');
CREATE TYPE event_kind      AS ENUM ('plenum','committee');
CREATE TYPE run_status      AS ENUM ('running','completed','failed');
CREATE TYPE role_type       AS ENUM (
  'prime_minister','minister','deputy_minister','knesset_speaker','deputy_speaker',
  'committee_chair','committee_member','faction_chair','coalition','opposition','other');
CREATE TYPE relation_kind   AS ENUM ('same_party','similar_positions','notable','ally','rival');

-- Keep updated_at honest.
CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN new.updated_at := now(); RETURN new; END $$;

-- ---------------------------------------------------------------------------
-- Reference / dimension tables
-- ---------------------------------------------------------------------------

-- NOTE: name_he is deliberately NOT unique. The export contains 6 repeated
-- party names (הליכוד, העבודה, יהדות התורה, יש עתיד, ישראל ביתנו, רע"ם) that are
-- distinct rows in the source data.
CREATE TABLE party (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name_he    text NOT NULL,
  name_en    text,
  short_name text,
  is_current boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX party_name_he_idx ON party (name_he);

CREATE TABLE committee (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  knesset_committee_id integer UNIQUE,
  name_he              text NOT NULL,
  name_en              text
);

CREATE TABLE issue (
  id                                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                              text NOT NULL UNIQUE,
  name                              text NOT NULL,
  description                       text,
  prompt_for_social_post_similarity text,
  prompt_for_bill_similarity        text,
  rating_scale                      jsonb,
  sort_order                        integer NOT NULL DEFAULT 0
);

-- Eight multilingual anchors per issue; curated and embedded once.
CREATE TABLE issue_anchor (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  issue_id        uuid NOT NULL REFERENCES issue (id) ON DELETE CASCADE,
  anchor_index    integer NOT NULL CHECK (anchor_index BETWEEN 1 AND 8),
  language        anchor_lang NOT NULL,
  text            text NOT NULL,
  embedding       real[],
  embedding_model text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (issue_id, anchor_index),
  CONSTRAINT issue_anchor_embedding_dim
    CHECK (embedding IS NULL OR cardinality(embedding) = 3072)
);

-- ---------------------------------------------------------------------------
-- MK, affiliations, roles, accounts
-- ---------------------------------------------------------------------------

-- NOTE: slug is NOT unique. The export contains 6 slug collisions between
-- genuinely different MKs who share a Hebrew name (e.g. אלי-כהן covers
-- knesset_member_id 755 and 30083). Disambiguating them is a product decision;
-- until then the URL key is ambiguous. knesset_member_id IS unique.
CREATE TABLE mk (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  knesset_member_id integer UNIQUE,
  slug              text NOT NULL,
  full_name_he      text NOT NULL,
  full_name_en      text,
  photo_url         text,
  birth_date        date,
  gender            text,
  home_city         text,
  bio_he            text,
  bio_en            text,
  is_current        boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mk_slug_idx       ON mk (slug);
CREATE INDEX mk_is_current_idx ON mk (is_current) WHERE is_current;
CREATE INDEX mk_name_trgm_idx  ON mk USING gin (full_name_he gin_trgm_ops);

CREATE TABLE mk_affiliation (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mk_id      uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  party_id   uuid NOT NULL REFERENCES party (id),
  start_date date,
  end_date   date,
  UNIQUE (mk_id, party_id),
  CONSTRAINT mk_affiliation_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);
CREATE INDEX mk_affiliation_current_idx ON mk_affiliation (mk_id) WHERE end_date IS NULL;

CREATE TABLE mk_role (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mk_id        uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  role_type    role_type NOT NULL,
  title_he     text,
  title_en     text,
  committee_id uuid REFERENCES committee (id),
  start_date   date,
  end_date     date
);
CREATE INDEX mk_role_current_idx ON mk_role (mk_id) WHERE end_date IS NULL;

CREATE TABLE mk_social_account (
  id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mk_id    uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  platform platform NOT NULL,
  handle   text NOT NULL,
  url      text,
  is_active boolean NOT NULL DEFAULT true,
  verified  boolean NOT NULL DEFAULT false,
  UNIQUE (platform, handle)
);
CREATE INDEX mk_social_account_mk_idx ON mk_social_account (mk_id) WHERE is_active;

-- ---------------------------------------------------------------------------
-- Posts and issue tagging
-- ---------------------------------------------------------------------------

-- mk_id and account_id foreign keys are declared NOT VALID: the export contains
-- 36 posts whose mk_id has no matching mk row, and 606 whose account_id has no
-- matching account. New writes are checked; run VALIDATE CONSTRAINT once the
-- legacy rows are reconciled (see db/pending_constraints.sql).
CREATE TABLE social_post (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mk_id            uuid NOT NULL,
  account_id       uuid,
  platform         platform NOT NULL,
  platform_post_id text NOT NULL,
  url              text,
  posted_at        timestamptz,
  text             text,
  embedding        real[],
  language         content_lang,
  engagement       jsonb,
  is_deleted       boolean NOT NULL DEFAULT false,
  fetched_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (platform, platform_post_id),
  CONSTRAINT social_post_embedding_dim
    CHECK (embedding IS NULL OR cardinality(embedding) = 3072)
);
ALTER TABLE social_post ADD CONSTRAINT social_post_mk_fk
  FOREIGN KEY (mk_id) REFERENCES mk (id) NOT VALID;
ALTER TABLE social_post ADD CONSTRAINT social_post_account_fk
  FOREIGN KEY (account_id) REFERENCES mk_social_account (id) NOT VALID;
CREATE INDEX social_post_mk_posted_idx ON social_post (mk_id, posted_at DESC);
CREATE INDEX social_post_posted_idx    ON social_post (posted_at DESC);
CREATE INDEX social_post_text_trgm_idx ON social_post USING gin (text gin_trgm_ops);

-- A post can touch several issues. Of 70,164 rows in the export only 3,808
-- clear confidence >= 0.2, which is the threshold the serving layer filters on
-- — hence the partial index.
CREATE TABLE post_issue (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id             uuid NOT NULL REFERENCES social_post (id) ON DELETE CASCADE,
  issue_id            uuid NOT NULL REFERENCES issue (id) ON DELETE CASCADE,
  confidence          numeric(4,3) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  is_concrete_promise boolean NOT NULL DEFAULT false,
  model_version       text,
  UNIQUE (post_id, issue_id)
);
CREATE INDEX post_issue_relevant_idx ON post_issue (issue_id, post_id) WHERE confidence >= 0.2;
CREATE INDEX post_issue_post_idx     ON post_issue (post_id);

CREATE TABLE tweet_cluster (
  model_version   text    NOT NULL,
  cluster_id      integer NOT NULL,
  centroid        real[],
  title_he        text NOT NULL,
  description_he  text NOT NULL,
  cluster_size    integer NOT NULL,
  is_garbage      boolean NOT NULL DEFAULT false,
  embedding_model text NOT NULL,
  summary_model   text NOT NULL,
  k               integer NOT NULL,
  random_state    integer NOT NULL,
  n_init          integer NOT NULL,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (model_version, cluster_id),
  CONSTRAINT tweet_cluster_centroid_dim
    CHECK (centroid IS NULL OR cardinality(centroid) = 3072)
);

-- ---------------------------------------------------------------------------
-- Per-(MK, issue) opinion summaries
-- ---------------------------------------------------------------------------

CREATE TABLE summary_generation_run (
  id            uuid PRIMARY KEY,
  mk_id         uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  model_version text NOT NULL,
  started_at    timestamptz NOT NULL,
  completed_at  timestamptz,
  elapsed_ms    bigint,
  status        run_status NOT NULL,
  issue_count   integer,
  post_count    integer,
  error_message text
);
CREATE INDEX summary_generation_run_mk_idx ON summary_generation_run (mk_id, started_at DESC);

-- quality/rating invariant: a rating exists exactly when quality is not 'none'.
-- Verified true for all 1,773 rows in the export.
CREATE TABLE mk_issue_summary (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mk_id               uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  issue_id            uuid NOT NULL REFERENCES issue (id) ON DELETE CASCADE,
  summary_he          text,
  extended_summary_he text,
  quality             summary_quality NOT NULL,
  rating              integer CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
  limitations         text,
  model_version       text,
  generation_run_id   uuid REFERENCES summary_generation_run (id) ON DELETE SET NULL,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (mk_id, issue_id),
  CONSTRAINT mk_issue_summary_rating_matches_quality
    CHECK ((quality = 'none' AND rating IS NULL) OR (quality <> 'none' AND rating IS NOT NULL))
);
CREATE INDEX mk_issue_summary_issue_idx ON mk_issue_summary (issue_id);

CREATE TABLE mk_issue_summary_supporting_post (
  summary_id uuid NOT NULL REFERENCES mk_issue_summary (id) ON DELETE CASCADE,
  post_id    uuid NOT NULL REFERENCES social_post (id) ON DELETE CASCADE,
  PRIMARY KEY (summary_id, post_id)
);
CREATE INDEX summary_supporting_post_idx ON mk_issue_summary_supporting_post (post_id);

-- ---------------------------------------------------------------------------
-- Bills, vote events, votes
-- ---------------------------------------------------------------------------

CREATE TABLE bill (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  knesset_bill_id          integer UNIQUE,
  title_he                 text,
  title_en                 text,
  summary                  text,
  status                   text,
  enacted_law_document_uri text,
  embedding                real[],
  CONSTRAINT bill_embedding_dim
    CHECK (embedding IS NULL OR cardinality(embedding) = 3072)
);

CREATE TABLE bill_author (
  bill_id uuid NOT NULL REFERENCES bill (id) ON DELETE CASCADE,
  mk_id   uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  role    text,
  PRIMARY KEY (bill_id, mk_id)
);

-- Anything MKs vote on. NOT PRESENT in the 2026-08-21 export — see
-- db/DATA_QUALITY.md. 907,210 mk_vote rows reference 34,159 events that must
-- be re-exported before the mk_vote foreign key can be enabled.
CREATE TABLE vote_event (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  external_key        text NOT NULL UNIQUE,
  event_kind          event_kind NOT NULL,
  bill_id             uuid REFERENCES bill (id) ON DELETE SET NULL,
  committee_id        uuid REFERENCES committee (id) ON DELETE SET NULL,
  reading             text,
  title_he            text,
  occurred_at         timestamptz,
  draft_document_uri  text
);
CREATE INDEX vote_event_bill_idx      ON vote_event (bill_id);
CREATE INDEX vote_event_committee_idx ON vote_event (committee_id);

CREATE TABLE bill_issue (
  bill_id        uuid NOT NULL REFERENCES bill (id) ON DELETE CASCADE,
  issue_id       uuid NOT NULL REFERENCES issue (id) ON DELETE CASCADE,
  mapping_method mapping_method,
  mapping_note   text,
  confidence     numeric(4,3) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  PRIMARY KEY (bill_id, issue_id)
);

CREATE TABLE vote_event_issue (
  vote_event_id  uuid NOT NULL REFERENCES vote_event (id) ON DELETE CASCADE,
  issue_id       uuid NOT NULL REFERENCES issue (id) ON DELETE CASCADE,
  mapping_method mapping_method,
  mapping_note   text,
  confidence     numeric(4,3) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  PRIMARY KEY (vote_event_id, issue_id)
);

-- The vote_event_id foreign key is added separately, after vote_event is
-- populated. See db/pending_constraints.sql.
CREATE TABLE mk_vote (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mk_id         uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  vote_event_id uuid NOT NULL,
  vote          vote_value NOT NULL,
  UNIQUE (mk_id, vote_event_id)
);
CREATE INDEX mk_vote_event_idx ON mk_vote (vote_event_id);

CREATE TABLE mk_relation (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mk_id         uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  related_mk_id uuid NOT NULL REFERENCES mk (id) ON DELETE CASCADE,
  relation_type relation_kind NOT NULL,
  issue_id      uuid REFERENCES issue (id) ON DELETE CASCADE,
  score         double precision,
  CONSTRAINT mk_relation_not_self CHECK (mk_id <> related_mk_id)
);

-- ---------------------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------------------
CREATE TRIGGER party_touch            BEFORE UPDATE ON party            FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER mk_touch               BEFORE UPDATE ON mk               FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER mk_issue_summary_touch BEFORE UPDATE ON mk_issue_summary FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER tweet_cluster_touch    BEFORE UPDATE ON tweet_cluster    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ---------------------------------------------------------------------------
-- Serving views (ported from db/schema.bq.sql)
-- ---------------------------------------------------------------------------

CREATE VIEW v_mk_card WITH (security_invoker = true) AS
SELECT
  m.id, m.slug, m.full_name_he, m.full_name_en, m.photo_url, m.is_current,
  p.name_he AS party_he,
  p.name_en AS party_en,
  ARRAY(SELECT DISTINCT sa.platform FROM mk_social_account sa
         WHERE sa.mk_id = m.id AND sa.is_active)      AS platforms,
  ARRAY(SELECT DISTINCT r.role_type FROM mk_role r
         WHERE r.mk_id = m.id AND r.end_date IS NULL) AS current_roles,
  (SELECT count(DISTINCT s.issue_id) FROM mk_issue_summary s
    WHERE s.mk_id = m.id)                             AS issue_count
FROM mk m
LEFT JOIN mk_affiliation a ON a.mk_id = m.id AND a.end_date IS NULL
LEFT JOIN party p ON p.id = a.party_id;

CREATE VIEW v_mk_issue_summary WITH (security_invoker = true) AS
SELECT
  s.id AS summary_id,
  s.mk_id, s.issue_id, i.slug AS issue_slug, i.name AS issue_name,
  i.description AS issue_description,
  s.summary_he, s.extended_summary_he, s.quality, s.rating, s.limitations,
  s.model_version, s.generation_run_id, s.updated_at,
  ARRAY(SELECT link.post_id FROM mk_issue_summary_supporting_post link
         WHERE link.summary_id = s.id)                AS supporting_post_ids,
  (SELECT count(*) FROM post_issue pi
     JOIN social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id AND pi.issue_id = s.issue_id)          AS post_count,
  (SELECT count(*) FROM post_issue pi
     JOIN social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id AND pi.issue_id = s.issue_id
      AND pi.is_concrete_promise)                                    AS promise_count
FROM mk_issue_summary s
JOIN issue i ON i.id = s.issue_id;

CREATE VIEW v_issue_landscape WITH (security_invoker = true) AS
SELECT
  i.id AS issue_id, i.slug AS issue_slug, i.name AS issue_name,
  s.mk_id, m.full_name_he, m.slug AS mk_slug,
  s.summary_he, s.extended_summary_he, s.quality, s.rating, s.limitations, s.updated_at,
  (SELECT count(*) FROM post_issue pi
     JOIN social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id AND pi.issue_id = i.id) AS post_count
FROM issue i
JOIN mk_issue_summary s ON s.issue_id = i.id
JOIN mk m ON m.id = s.mk_id;

CREATE VIEW v_vote_event_issues WITH (security_invoker = true) AS
SELECT vei.vote_event_id, vei.issue_id FROM vote_event_issue vei
UNION
SELECT e.id AS vote_event_id, bi.issue_id
FROM vote_event e
JOIN bill_issue bi ON bi.bill_id = e.bill_id;

CREATE VIEW v_mk_issue_authorship WITH (security_invoker = true) AS
SELECT
  ba.mk_id, bi.issue_id,
  count(*) FILTER (WHERE ba.role = 'initiator')    AS bills_initiated,
  count(*) FILTER (WHERE ba.role = 'co_initiator') AS bills_co_initiated,
  count(*)                                         AS bills_authored_total
FROM bill_author ba
JOIN bill_issue bi ON bi.bill_id = ba.bill_id
GROUP BY ba.mk_id, bi.issue_id;

CREATE VIEW v_said_vs_did WITH (security_invoker = true) AS
SELECT
  v.mk_id, m.issue_id, e.event_kind,
  count(*) FILTER (WHERE v.vote = 'for')     AS votes_for,
  count(*) FILTER (WHERE v.vote = 'against') AS votes_against,
  count(*) FILTER (WHERE v.vote = 'abstain') AS votes_abstain,
  count(*) FILTER (WHERE v.vote = 'absent')  AS votes_absent
FROM mk_vote v
JOIN vote_event e ON e.id = v.vote_event_id
JOIN v_vote_event_issues m ON m.vote_event_id = e.id
GROUP BY v.mk_id, m.issue_id, e.event_kind;
