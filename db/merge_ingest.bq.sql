-- ============================================================================
-- mk-tracking — batch ingestion MERGEs (BigQuery) — v3
-- ============================================================================
-- Runs after staging tables are loaded (see docs/INGESTION_CONTRACT.md §3):
--   stg_posts (posts with inline issue tags), stg_summaries
-- Idempotent: MERGE on natural keys; safe to re-run.
-- Order matters: posts -> post_issue -> summaries -> summary evidence.
-- ============================================================================

-- (1) social posts — key (platform, platform_post_id)
MERGE mk_tracking.social_post t
USING (
  SELECT s.*, m.id AS resolved_mk_id
  FROM mk_tracking.stg_posts s
  JOIN mk_tracking.mk m ON m.knesset_member_id = s.knesset_member_id
) s
ON t.platform = s.platform AND t.platform_post_id = s.platform_post_id
WHEN MATCHED THEN UPDATE SET
  text = s.text, embedding = s.embedding, engagement = s.engagement,
  is_deleted = COALESCE(s.is_deleted, FALSE), fetched_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (id, mk_id, platform, platform_post_id, url, posted_at, text, embedding, language, engagement, is_deleted, fetched_at)
  VALUES (GENERATE_UUID(), s.resolved_mk_id, s.platform, s.platform_post_id, s.url, s.posted_at, s.text, s.embedding, s.language, s.engagement, COALESCE(s.is_deleted, FALSE), CURRENT_TIMESTAMP());

-- (2) post <-> issue tags — key (post_id, issue_id); unnests inline issues[]
MERGE mk_tracking.post_issue t
USING (
  SELECT p.id AS post_id, i.id AS issue_id,
         CAST(iss.confidence AS NUMERIC) AS confidence,
         COALESCE(iss.is_concrete_promise, FALSE) AS is_concrete_promise,
         s.model_version
  FROM mk_tracking.stg_posts s, UNNEST(s.issues) iss
  JOIN mk_tracking.issue i ON i.slug = iss.slug
  JOIN mk_tracking.social_post p
    ON p.platform = s.platform AND p.platform_post_id = s.platform_post_id
) s
ON t.post_id = s.post_id AND t.issue_id = s.issue_id
WHEN MATCHED THEN UPDATE SET
  confidence = s.confidence, is_concrete_promise = s.is_concrete_promise,
  model_version = s.model_version
WHEN NOT MATCHED THEN INSERT (id, post_id, issue_id, confidence, is_concrete_promise, model_version)
  VALUES (GENERATE_UUID(), s.post_id, s.issue_id, s.confidence, s.is_concrete_promise, s.model_version);

-- (3) per-(MK, issue) SLM summaries — key (mk_id, issue_id), overwrite semantics
MERGE mk_tracking.mk_issue_summary t
USING (
  SELECT m.id AS mk_id, i.id AS issue_id,
         s.summary_he, s.quality, s.rating, s.limitations, s.model_version
  FROM mk_tracking.stg_summaries s
  JOIN mk_tracking.mk m ON m.knesset_member_id = s.knesset_member_id
  JOIN mk_tracking.issue i ON i.slug = s.issue_slug
) s
ON t.mk_id = s.mk_id AND t.issue_id = s.issue_id
WHEN MATCHED THEN UPDATE SET
  summary_he = s.summary_he, quality = s.quality, rating = s.rating,
  limitations = s.limitations,
  model_version = s.model_version, updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (id, mk_id, issue_id, summary_he, quality, rating, limitations, model_version)
  VALUES (GENERATE_UUID(), s.mk_id, s.issue_id, s.summary_he, s.quality, s.rating, s.limitations, s.model_version);

-- (4) summary -> supporting posts — replace the evidence set for every staged
-- summary before inserting its current links. This also clears evidence for a
-- summary whose new quality is none.
DELETE FROM mk_tracking.mk_issue_summary_supporting_post link
WHERE link.summary_id IN (
  SELECT sm.id
  FROM mk_tracking.stg_summaries s
  JOIN mk_tracking.mk m ON m.knesset_member_id = s.knesset_member_id
  JOIN mk_tracking.issue i ON i.slug = s.issue_slug
  JOIN mk_tracking.mk_issue_summary sm ON sm.mk_id = m.id AND sm.issue_id = i.id
);

-- Only posts explicitly supplied by the summarizer are linked. Validation must
-- ensure quality strong/partial has evidence and quality none has none.
MERGE mk_tracking.mk_issue_summary_supporting_post t
USING (
  SELECT DISTINCT sm.id AS summary_id, p.id AS post_id
  FROM mk_tracking.stg_summaries s, UNNEST(s.supporting_posts) supporting
  JOIN mk_tracking.mk m ON m.knesset_member_id = s.knesset_member_id
  JOIN mk_tracking.issue i ON i.slug = s.issue_slug
  JOIN mk_tracking.mk_issue_summary sm ON sm.mk_id = m.id AND sm.issue_id = i.id
  JOIN mk_tracking.social_post p
    ON p.platform = supporting.platform
   AND p.platform_post_id = supporting.platform_post_id
   AND p.mk_id = m.id
) s
ON t.summary_id = s.summary_id AND t.post_id = s.post_id
WHEN NOT MATCHED THEN INSERT (summary_id, post_id)
  VALUES (s.summary_id, s.post_id);
