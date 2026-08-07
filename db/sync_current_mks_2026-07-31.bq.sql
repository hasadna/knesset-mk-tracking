-- Synchronize mk.is_current to the official Knesset roster snapshot.
--
-- Authority:
-- https://knesset.gov.il/Odata/ParliamentInfo.svc/KNS_PersonToPosition()
-- Filter:
--   KnessetNum eq 25 and PositionID eq 54 and IsCurrent eq true
-- Retrieved: 2026-07-31
--
-- PositionID 54 is the faction-membership record. The official response is
-- paginated at 100 rows; both pages returned 120 unique PersonID values.

BEGIN TRANSACTION;

CREATE TEMP TABLE official_current_mk (
  knesset_member_id INT64 NOT NULL
);

INSERT INTO official_current_mk (knesset_member_id)
VALUES
  (427), (468), (526), (532), (560), (563), (965), (1025), (1056), (2291),
  (4395), (4397), (11835), (12938), (12951), (22151), (23551), (23558),
  (23560), (23565), (23591), (23594), (23597), (23631), (23632), (23635),
  (23641), (28513), (30066), (30067), (30102), (30106), (30118), (30121),
  (30300), (30470), (30601), (30657), (30671), (30672), (30682), (30683),
  (30685), (30686), (30691), (30693), (30694), (30695), (30700), (30701),
  (30702), (30704), (30705), (30706), (30708), (30710), (30711), (30713),
  (30717), (30719), (30720), (30722), (30749), (30752), (30758), (30765),
  (30770), (30772), (30775), (30776), (30777), (30782), (30783), (30799),
  (30804), (30807), (30808), (30809), (30810), (30811), (30812), (30814),
  (30820), (30830), (30831), (30832), (30835), (30837), (30839), (30840),
  (30842), (30843), (30846), (30847), (30849), (30851), (30852), (30853),
  (30854), (30857), (30859), (30860), (30861), (30863), (30867), (30868),
  (30871), (30873), (30874), (30875), (30876), (30877), (30879), (30880),
  (30881), (30893), (30894), (30895), (30916), (30917);

ASSERT (
  SELECT COUNT(*) = 120
  FROM official_current_mk
) AS 'Official current-MK snapshot must contain exactly 120 IDs';

ASSERT (
  SELECT COUNT(*) = 120
  FROM mk_tracking.mk AS m
  JOIN official_current_mk AS official
    ON official.knesset_member_id = m.knesset_member_id
) AS 'Every official current MK must exist exactly once in mk_tracking.mk';

UPDATE mk_tracking.mk AS m
SET
  is_current = EXISTS (
    SELECT 1
    FROM official_current_mk AS official
    WHERE official.knesset_member_id = m.knesset_member_id
  ),
  updated_at = CURRENT_TIMESTAMP()
WHERE m.is_current IS DISTINCT FROM EXISTS (
  SELECT 1
  FROM official_current_mk AS official
  WHERE official.knesset_member_id = m.knesset_member_id
);

ASSERT (
  SELECT COUNTIF(is_current) = 120
  FROM mk_tracking.mk
) AS 'Exactly 120 MK rows must be current after synchronization';

COMMIT TRANSACTION;
