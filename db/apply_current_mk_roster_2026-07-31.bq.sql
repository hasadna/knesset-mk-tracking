-- Apply the user-curated 120-member roster in current_mk_roster_2026-07-31.tsv.
--
-- Prerequisite: batch-load the TSV into
--   mk_tracking.stg_current_mk_roster_20260731
-- with STRING columns full_name_he and party_name_he.
--
-- This migration:
--   * makes the staged roster the sole authority for mk.is_current;
--   * preserves one correct open affiliation per current MK;
--   * closes every other open affiliation as of the snapshot date;
--   * inserts a current affiliation where one does not already exist.

ASSERT (
  SELECT COUNT(*) = 120
    AND COUNT(DISTINCT full_name_he) = 120
    AND COUNT(DISTINCT party_name_he) = 13
  FROM mk_tracking.stg_current_mk_roster_20260731
) AS 'Roster must contain 120 unique MK names and 13 parties';

CREATE TEMP TABLE resolved_roster AS
WITH roster_names AS (
  SELECT
    full_name_he AS roster_name_he,
    party_name_he,
    CASE full_name_he
      WHEN 'בני גנץ' THEN 'בנימין גנץ'
      WHEN 'צגה צגנש מלקו' THEN 'צגה מלקו'
      WHEN 'יואב סגלוביץ' THEN CONCAT('יואב סגלוביץ', CHR(39))
      WHEN 'אורית סטרוק' THEN 'אורית מלכה סטרוק'
      WHEN 'יצחק זאב פינדרוס' THEN 'יצחק פינדרוס'
      ELSE full_name_he
    END AS stored_name_he
  FROM mk_tracking.stg_current_mk_roster_20260731
),
canonical_party AS (
  SELECT id, name_he
  FROM mk_tracking.party
  WHERE name_he IN (
    SELECT DISTINCT party_name_he
    FROM mk_tracking.stg_current_mk_roster_20260731
  )
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY name_he
    ORDER BY id
  ) = 1
)
SELECT
  r.roster_name_he,
  r.party_name_he,
  m.id AS mk_id,
  m.knesset_member_id,
  p.id AS party_id
FROM roster_names AS r
JOIN mk_tracking.mk AS m
  ON m.full_name_he = r.stored_name_he
 AND (r.roster_name_he != 'ישראל כץ' OR m.knesset_member_id = 468)
JOIN canonical_party AS p
  ON p.name_he = r.party_name_he;

ASSERT (
  SELECT COUNT(*) = 120
    AND COUNT(DISTINCT mk_id) = 120
    AND COUNT(DISTINCT roster_name_he) = 120
    AND COUNTIF(party_id IS NULL) = 0
  FROM resolved_roster
) AS 'Every roster entry must resolve to exactly one MK and one party';

CREATE TABLE IF NOT EXISTS
  mk_tracking._backup_mk_current_pre_roster_20260731
AS
SELECT id, knesset_member_id, full_name_he, is_current, updated_at
FROM mk_tracking.mk;

CREATE TABLE IF NOT EXISTS
  mk_tracking._backup_mk_affiliation_pre_roster_20260731
AS
SELECT *
FROM mk_tracking.mk_affiliation;

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
SET end_date = DATE '2026-07-31'
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
  DATE '2026-07-31',
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
