-- ============================================================================
-- One-time destructive migration: v4 issue taxonomy -> v5 seven-axis taxonomy
--
-- All derived issue classifications, summaries, anchors and mappings are
-- invalid after this semantic reset. Source posts, bills, votes and MKs remain.
-- Run db/seed.bq.sql immediately afterward to install the canonical seven rows.
-- ============================================================================

CREATE TABLE IF NOT EXISTS mk_tracking._backup_issue_pre_v5_20260731
AS SELECT * FROM mk_tracking.issue;

CREATE TABLE IF NOT EXISTS mk_tracking._backup_issue_anchor_pre_v5_20260731
AS SELECT * FROM mk_tracking.issue_anchor;

CREATE TABLE IF NOT EXISTS mk_tracking._backup_post_issue_pre_v5_20260731
AS SELECT * FROM mk_tracking.post_issue;

CREATE TABLE IF NOT EXISTS mk_tracking._backup_mk_issue_summary_pre_v5_20260731
AS SELECT * FROM mk_tracking.mk_issue_summary;

CREATE TABLE IF NOT EXISTS mk_tracking._backup_summary_support_pre_v5_20260731
AS SELECT * FROM mk_tracking.mk_issue_summary_supporting_post;

CREATE TABLE IF NOT EXISTS mk_tracking._backup_bill_issue_pre_v5_20260731
AS SELECT * FROM mk_tracking.bill_issue;

CREATE TABLE IF NOT EXISTS mk_tracking._backup_vote_event_issue_pre_v5_20260731
AS SELECT * FROM mk_tracking.vote_event_issue;

CREATE TABLE IF NOT EXISTS mk_tracking._backup_mk_relation_issue_pre_v5_20260731
AS SELECT * FROM mk_tracking.mk_relation WHERE issue_id IS NOT NULL;

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
