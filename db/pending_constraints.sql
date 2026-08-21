-- ============================================================================
-- mk-tracking — constraints that the current data cannot yet satisfy
-- ============================================================================
-- db/schema.sql declares these NOT VALID (or omits them), so new writes are
-- checked while legacy rows are left alone. Run the matching block once the
-- underlying data is repaired. Each is a no-op if it already passes.
-- Measured against mk_tracking.db, 2026-08-21 — see db/DATA_QUALITY.md.
-- ============================================================================

-- (1) social_post.mk_id -> mk
--     9 posts carry a UUID-shaped mk_id with no matching mk row. (A further 27
--     held a Twitter handle instead of a UUID; the loader repairs those.)
--     Repair or delete the 9, then:
-- ALTER TABLE mk_tracking.social_post VALIDATE CONSTRAINT social_post_mk_fk;

-- (2) social_post.account_id -> mk_social_account
--     597 posts stored the platform-native numeric account id instead of the
--     resolved UUID; the loader nulls them because mk_social_account has no
--     column holding the numeric id, so they cannot be resolved from the export.
--     Add that column upstream, backfill, then:
-- ALTER TABLE mk_tracking.social_post VALIDATE CONSTRAINT social_post_account_fk;

-- (3) mk_vote.vote_event_id -> vote_event
--     vote_event and vote_event_issue are absent from the export, so all 907,210
--     vote rows reference 34,159 events that do not exist locally. Once both
--     tables are re-exported and loaded, add the key for real:
-- ALTER TABLE mk_tracking.mk_vote
--   ADD CONSTRAINT mk_vote_event_fk
--   FOREIGN KEY (vote_event_id) REFERENCES mk_tracking.vote_event (id) ON DELETE CASCADE;

-- (4) mk.slug uniqueness
--     6 slugs are shared by genuinely different MKs (e.g. אלי-כהן covers
--     knesset_member_id 755 and 30083), so the URL key is ambiguous. This needs
--     a disambiguation rule before it can be enforced:
-- ALTER TABLE mk_tracking.mk ADD CONSTRAINT mk_slug_key UNIQUE (slug);
