-- ============================================================================
-- Roster Application Pipeline Script — `db/apply_current_mk_roster.bq.sql`
-- ============================================================================
-- Applies a curated 120-member Knesset roster snapshot to the production database.
-- This is done because the Knesset/OVER data is not fully correct, and some iscurrent markings are outdated,
-- so we need to decide by ourselves which MK is in which party.
--
-- 📌 PREREQUISITES:
--   1. The primary Knesset/OVER data pipeline (e.g. upload_over_to_bigquery.py) MUST
--      have already loaded person records into `mk_tracking.mk` and party records
--      into `mk_tracking.party`. This script DOES NOT create new MK person rows.
--   2. A curated 120-MK roster TSV file (e.g. data/seed/current_mk_roster_YYYY-MM-DD.tsv)
--      MUST be batch-loaded into the staging table:
--        `mk_tracking.stg_current_mk_roster`
--      with columns:
--        knesset_member_id INT64, full_name_he STRING, party_name_he STRING
--
-- ✅ WHAT THIS SCRIPT DOES:
--   * Makes the staged 120-member roster the sole authority for `mk.is_current`.
--   * Sets `mk.is_current = TRUE` for all 120 staged MKs.
--   * Sets `mk.is_current = FALSE` for any former / non-sitting MKs.
--   * Preserves / verifies one active open party affiliation (`end_date IS NULL`) per current MK.
--   * Closes outdated party affiliations as of `CURRENT_DATE()`.
--   * Inserts new `mk_affiliation` records as of `CURRENT_DATE()` where an open affiliation is missing.
--
-- ❌ WHAT THIS SCRIPT DOES NOT DO:
--   * Does NOT create or insert new person profiles into `mk_tracking.mk` (fails loudly via ASSERT if an MK ID is missing).
--   * Does NOT mutate core identity fields (photo, bio, names) derived from Knesset OData.
-- ============================================================================

ASSERT (
  SELECT COUNT(*) = 120
    AND COUNT(DISTINCT CAST(knesset_member_id AS INT64)) = 120
  FROM mk_tracking.stg_current_mk_roster
) AS 'Roster must contain exactly 120 unique MK IDs';

CREATE TEMP TABLE resolved_roster AS
WITH canonical_party AS (
  SELECT id, name_he
  FROM mk_tracking.party
  WHERE name_he IN (
    SELECT DISTINCT party_name_he
    FROM mk_tracking.stg_current_mk_roster
  )
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY name_he
    ORDER BY id
  ) = 1
)
SELECT
  stg.knesset_member_id,
  stg.full_name_he AS roster_name_he,
  stg.party_name_he,
  m.id AS mk_id,
  p.id AS party_id
FROM mk_tracking.stg_current_mk_roster AS stg
JOIN mk_tracking.mk AS m
  ON m.knesset_member_id = CAST(stg.knesset_member_id AS INT64)
JOIN canonical_party AS p
  ON p.name_he = stg.party_name_he;

ASSERT (
  SELECT COUNT(*) = 120
    AND COUNT(DISTINCT mk_id) = 120
    AND COUNTIF(party_id IS NULL) = 0
  FROM resolved_roster
) AS 'Every roster entry must resolve to exactly one MK and one party';

CREATE TEMP TABLE open_affiliation_decisions AS
SELECT
  a.id AS affiliation_id,
  r.mk_id IS NOT NULL
    AND a.party_id = r.party_id
    AND ROW_NUMBER() OVER (
      PARTITION BY a.mk_id, a.party_id
      ORDER BY a.id
    ) = 1 AS keep_open
FROM mk_tracking.mk_affiliation AS a
LEFT JOIN resolved_roster AS r
  ON r.mk_id = a.mk_id
WHERE a.end_date IS NULL;

BEGIN TRANSACTION;

UPDATE mk_tracking.mk AS m
SET
  is_current = EXISTS (
    SELECT 1
    FROM resolved_roster AS r
    WHERE r.mk_id = m.id
  ),
  updated_at = CURRENT_TIMESTAMP()
WHERE m.is_current IS DISTINCT FROM EXISTS (
  SELECT 1
  FROM resolved_roster AS r
  WHERE r.mk_id = m.id
);

UPDATE mk_tracking.mk_affiliation AS a
SET end_date = CURRENT_DATE()
WHERE a.id IN (
  SELECT affiliation_id
  FROM open_affiliation_decisions
  WHERE NOT keep_open
);

INSERT INTO mk_tracking.mk_affiliation (
  id,
  mk_id,
  party_id,
  start_date,
  end_date
)
SELECT
  GENERATE_UUID(),
  r.mk_id,
  r.party_id,
  CURRENT_DATE(),
  NULL
FROM resolved_roster AS r
WHERE NOT EXISTS (
  SELECT 1
  FROM mk_tracking.mk_affiliation AS a
  WHERE a.mk_id = r.mk_id
    AND a.party_id = r.party_id
    AND a.end_date IS NULL
);

ASSERT (
  SELECT COUNTIF(is_current) = 120
  FROM mk_tracking.mk
) AS 'Exactly 120 MKs must be current';

ASSERT (
  SELECT COUNT(*) = 120
    AND COUNT(DISTINCT a.mk_id) = 120
    AND COUNTIF(a.party_id != r.party_id) = 0
  FROM mk_tracking.mk_affiliation AS a
  JOIN resolved_roster AS r
    ON r.mk_id = a.mk_id
  WHERE a.end_date IS NULL
) AS 'Every current MK must have exactly one correct open affiliation';

ASSERT (
  SELECT COUNT(*) = 0
  FROM mk_tracking.mk_affiliation AS a
  JOIN mk_tracking.mk AS m
    ON m.id = a.mk_id
  WHERE a.end_date IS NULL
    AND NOT m.is_current
) AS 'Non-current people must not retain open affiliations';

COMMIT TRANSACTION;
