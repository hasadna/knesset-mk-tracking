-- Production-safe X post ingestion from the explicitly typed stg_x_posts table.
-- Natural key: (platform, platform_post_id). Safe to rerun.

MERGE mk_tracking.social_post AS target
USING mk_tracking.stg_x_posts AS source
ON target.platform = source.platform
AND target.platform_post_id = source.platform_post_id
WHEN MATCHED THEN UPDATE SET
  mk_id = source.mk_id,
  account_id = source.account_id,
  url = source.url,
  posted_at = source.posted_at,
  text = source.text,
  language = source.language,
  engagement = source.engagement,
  is_deleted = FALSE,
  fetched_at = source.fetched_at
WHEN NOT MATCHED THEN INSERT (
  id, mk_id, account_id, platform, platform_post_id, url, posted_at,
  text, language, engagement, is_deleted, fetched_at, created_at
)
VALUES (
  source.id, source.mk_id, source.account_id, source.platform,
  source.platform_post_id, source.url, source.posted_at, source.text,
  source.language, source.engagement, source.is_deleted, source.fetched_at,
  source.created_at
);
