-- Applied on 2026-07-30. Only repairs an orphan when the MK has exactly one
-- active Twitter account. Ambiguous rows are deliberately left unchanged.
CREATE TABLE IF NOT EXISTS social_post_account_repair_backup_20260730 AS
SELECT p.*
FROM social_post p
LEFT JOIN mk_social_account old_account ON old_account.id = p.account_id
JOIN (
  SELECT mk_id, ANY_VALUE(id) AS account_id
  FROM mk_social_account
  WHERE platform = 'twitter' AND is_active
  GROUP BY mk_id
  HAVING COUNT(*) = 1
) replacement ON replacement.mk_id = p.mk_id
WHERE p.platform = 'twitter'
  AND NOT p.is_deleted
  AND old_account.id IS NULL;

UPDATE social_post p
SET account_id = replacement.account_id
FROM (
  SELECT mk_id, ANY_VALUE(id) AS account_id
  FROM mk_social_account
  WHERE platform = 'twitter' AND is_active
  GROUP BY mk_id
  HAVING COUNT(*) = 1
) replacement
WHERE p.mk_id = replacement.mk_id
  AND p.platform = 'twitter'
  AND NOT p.is_deleted
  AND NOT EXISTS (
    SELECT 1
    FROM mk_social_account current_account
    WHERE current_account.id = p.account_id
  );
