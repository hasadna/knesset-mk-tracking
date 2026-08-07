-- Add the second batch of manually verified X accounts for current MKs.
-- Safe to rerun: matches the documented natural key (mk_id, platform, handle).
-- Each profile resolved through the live X API on 2026-07-31, explicitly
-- identified the MK, and was corroborated by public attribution/activity.

BEGIN TRANSACTION;

MERGE mk_tracking.mk_social_account AS target
USING (
  SELECT
    mk.id AS mk_id,
    candidate.handle,
    FORMAT('https://x.com/%s', candidate.handle) AS url
  FROM UNNEST([
    STRUCT(1057 AS knesset_member_id, 'YisraelEichler' AS handle),
    (30058, 'zoharm7'),
    (23635, 'pnina_tamano_sh'),
    (30820, 'simondav14'),
    (30863, 'SharonNir11'),
    (30849, 'limor_sonhrmelh'),
    (30859, 'tzvikafoghel'),
    (30843, 'YasserHujerat'),
    (30893, 'afef_abed_'),
    (30895, 'AdiEzuz')
  ]) AS candidate
  JOIN mk_tracking.mk AS mk
    USING (knesset_member_id)
  WHERE mk.is_current
) AS source
ON target.mk_id = source.mk_id
AND target.platform = 'twitter'
AND LOWER(target.handle) = LOWER(source.handle)
WHEN MATCHED THEN
  UPDATE SET
    url = source.url,
    is_active = TRUE
WHEN NOT MATCHED THEN
  INSERT (id, mk_id, platform, handle, url, is_active, verified)
  VALUES (GENERATE_UUID(), source.mk_id, 'twitter', source.handle, source.url, TRUE, FALSE);

ASSERT (
  SELECT COUNT(*)
  FROM mk_tracking.mk_social_account
  WHERE platform = 'twitter'
    AND is_active
    AND LOWER(handle) IN (
      'yisraeleichler', 'zoharm7', 'pnina_tamano_sh', 'simondav14',
      'sharonnir11', 'limor_sonhrmelh', 'tzvikafoghel', 'yasserhujerat',
      'afef_abed_', 'adiezuz'
    )
) = 10 AS 'Expected all 10 verified X accounts to be active';

ASSERT (
  SELECT COUNT(*)
  FROM (
    SELECT LOWER(handle)
    FROM mk_tracking.mk_social_account
    WHERE platform = 'twitter'
      AND LOWER(handle) IN (
        'yisraeleichler', 'zoharm7', 'pnina_tamano_sh', 'simondav14',
        'sharonnir11', 'limor_sonhrmelh', 'tzvikafoghel', 'yasserhujerat',
        'afef_abed_', 'adiezuz'
      )
    GROUP BY LOWER(handle)
    HAVING COUNT(*) > 1
  )
) = 0 AS 'Duplicate X handles detected';

COMMIT TRANSACTION;
