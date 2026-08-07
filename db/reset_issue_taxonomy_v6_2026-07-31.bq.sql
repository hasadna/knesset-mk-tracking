-- ============================================================================
-- One-time destructive migration: v5 seven-axis taxonomy -> v6 nine-issue
-- taxonomy (docs/ISSUES.md, team consensus 2026-07-31).
--
-- All derived issue classifications, summaries, anchors and mappings are
-- invalid after this semantic reset. Source posts, bills, votes and MKs remain.
-- Run db/seed.bq.sql immediately afterward to install the canonical nine rows.
--
-- BACKUP: the complete pre-v6 dataset is snapshotted in
-- `mk_tracking_snap_20260731` (all 21 serving tables, created 2026-07-31,
-- before this migration). The UI is pinned to that snapshot until v6 scores
-- and summaries are regenerated. No additional per-table backups are created
-- here — the snapshot dataset is the restore point.
-- ============================================================================

BEGIN TRANSACTION;

DELETE FROM mk_tracking.mk_issue_summary_supporting_post
WHERE summary_id IN (SELECT id FROM mk_tracking.mk_issue_summary);

DELETE FROM mk_tracking.mk_issue_summary WHERE TRUE;
DELETE FROM mk_tracking.post_issue WHERE TRUE;
DELETE FROM mk_tracking.bill_issue WHERE TRUE;
DELETE FROM mk_tracking.vote_event_issue WHERE TRUE;
DELETE FROM mk_tracking.issue_anchor WHERE TRUE;
DELETE FROM mk_tracking.mk_relation WHERE issue_id IS NOT NULL;
DELETE FROM mk_tracking.issue WHERE TRUE;

COMMIT TRANSACTION;
