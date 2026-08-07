-- Additive v5 migration: issue-specific 1..5 scales and summary ratings.
-- Safe to rerun. Existing summaries remain NULL until regenerated.

ALTER TABLE mk_tracking.issue
ADD COLUMN IF NOT EXISTS rating_scale JSON;

ALTER TABLE mk_tracking.mk_issue_summary
ADD COLUMN IF NOT EXISTS rating INT64
OPTIONS (description = '1..5 on issue.rating_scale; NULL when quality=none');
