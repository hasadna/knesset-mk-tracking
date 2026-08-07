-- One-time guarded merge of the reviewed vote-mkid backfill batch.
-- Prerequisite: stg_mk_vote_temp contains the 485,724 rows produced from
-- db/vote_mkid_crosswalk.json for the 89 mismatched identities.

ASSERT (
  SELECT COUNT(*) = 485724
  FROM mk_tracking.stg_mk_vote_temp
) AS 'Expected the complete reviewed vote-mkid staging batch';

BEGIN TRANSACTION;

ASSERT (
  SELECT COUNT(*) = 485724
  FROM mk_tracking.stg_mk_vote_temp stg
  JOIN mk_tracking.mk m
    ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
  JOIN mk_tracking.vote_event ve
    ON ve.external_key = stg.external_key
) AS 'Every staged vote must resolve to one MK and one vote event';

MERGE mk_tracking.mk_vote target
USING (
  SELECT
    m.id AS mk_id,
    ve.id AS vote_event_id,
    stg.vote
  FROM mk_tracking.stg_mk_vote_temp stg
  JOIN mk_tracking.mk m
    ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
  JOIN mk_tracking.vote_event ve
    ON ve.external_key = stg.external_key
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY m.id, ve.id
    ORDER BY stg.vote
  ) = 1
) source
ON target.mk_id = source.mk_id
AND target.vote_event_id = source.vote_event_id
WHEN MATCHED THEN
  UPDATE SET vote = source.vote
WHEN NOT MATCHED THEN
  INSERT (id, mk_id, vote_event_id, vote)
  VALUES (GENERATE_UUID(), source.mk_id, source.vote_event_id, source.vote);

ASSERT (
  SELECT COUNT(*) = 485724
  FROM mk_tracking.stg_mk_vote_temp stg
  JOIN mk_tracking.mk m
    ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
  JOIN mk_tracking.vote_event ve
    ON ve.external_key = stg.external_key
  JOIN mk_tracking.mk_vote target
    ON target.mk_id = m.id
   AND target.vote_event_id = ve.id
) AS 'Every staged vote must exist after merge';

COMMIT TRANSACTION;
