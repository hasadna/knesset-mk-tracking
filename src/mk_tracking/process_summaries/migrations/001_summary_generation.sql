-- Applied to $GOOGLE_CLOUD_PROJECT.mk_tracking on 2026-07-30.
ALTER TABLE mk_issue_summary
ADD COLUMN IF NOT EXISTS extended_summary_he STRING;

ALTER TABLE mk_issue_summary
ADD COLUMN IF NOT EXISTS generation_run_id STRING;

CREATE TABLE IF NOT EXISTS summary_generation_run (
  id STRING NOT NULL,
  mk_id STRING NOT NULL,
  model_version STRING NOT NULL,
  started_at TIMESTAMP NOT NULL,
  completed_at TIMESTAMP,
  elapsed_ms INT64,
  status STRING NOT NULL,
  issue_count INT64,
  post_count INT64,
  error_message STRING
)
CLUSTER BY mk_id;

CREATE OR REPLACE VIEW v_mk_issue_summary AS
SELECT
  s.mk_id,
  s.issue_id,
  i.slug AS issue_slug,
  i.name AS issue_name,
  s.summary_he,
  s.extended_summary_he,
  s.quality,
  s.limitations,
  s.model_version,
  s.generation_run_id,
  s.updated_at,
  (
    SELECT COUNT(*)
    FROM post_issue pi
    JOIN social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id AND pi.issue_id = s.issue_id
  ) AS post_count,
  (
    SELECT COUNT(*)
    FROM post_issue pi
    JOIN social_post sp ON sp.id = pi.post_id
    WHERE sp.mk_id = s.mk_id
      AND pi.issue_id = s.issue_id
      AND pi.is_concrete_promise
  ) AS promise_count
FROM mk_issue_summary s
JOIN issue i ON i.id = s.issue_id;
