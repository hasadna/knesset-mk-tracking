-- ============================================================================
-- Enforce the v5 issue ground truth (docs/ISSUES.md).
-- Removes only off-list issues and their dependent derived rows.
-- For the one-time semantic reset from v4, use
-- db/reset_issue_taxonomy_v5_2026-07-31.bq.sql instead.
-- ============================================================================

CREATE TEMP TABLE off_list_issue AS
SELECT id
FROM mk_tracking.issue
WHERE slug NOT IN (
  'religion-state',
  'economy-welfare',
  'security-peace',
  'judiciary',
  'netanyahu-leadership',
  'haredi-conscription',
  'discourse-quality'
);

DELETE FROM mk_tracking.mk_issue_summary_supporting_post
WHERE summary_id IN (
  SELECT id
  FROM mk_tracking.mk_issue_summary
  WHERE issue_id IN (SELECT id FROM off_list_issue)
);

DELETE FROM mk_tracking.mk_issue_summary
WHERE issue_id IN (SELECT id FROM off_list_issue);

DELETE FROM mk_tracking.post_issue
WHERE issue_id IN (SELECT id FROM off_list_issue);

DELETE FROM mk_tracking.bill_issue
WHERE issue_id IN (SELECT id FROM off_list_issue);

DELETE FROM mk_tracking.vote_event_issue
WHERE issue_id IN (SELECT id FROM off_list_issue);

DELETE FROM mk_tracking.issue_anchor
WHERE issue_id IN (SELECT id FROM off_list_issue);

DELETE FROM mk_tracking.mk_relation
WHERE issue_id IN (SELECT id FROM off_list_issue);

DELETE FROM mk_tracking.issue
WHERE id IN (SELECT id FROM off_list_issue);
