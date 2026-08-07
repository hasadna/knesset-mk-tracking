-- ============================================================================
-- Seed data — predetermined issue/policy areas
-- ============================================================================
-- This is the closed list the sentiment team tags quotes against. Edit to match
-- the team's agreed taxonomy. `slug` is the stable machine key — do not rename
-- once the pipeline references it; change display names freely.
--
-- Apply after schema.sql:  psql "$DATABASE_URL" -f db/seed.sql
-- Idempotent: re-running upserts by slug.
-- ============================================================================

insert into issue (slug, name_he, name_en, sort_order) values
  ('economy',          'כלכלה',                'Economy',                  10),
  ('housing',          'דיור',                 'Housing',                  20),
  ('security-defense', 'ביטחון',               'Security & Defense',       30),
  ('foreign-policy',   'מדיניות חוץ',          'Foreign Policy',           40),
  ('judiciary',        'מערכת המשפט',          'Judiciary & Rule of Law',  50),
  ('religion-state',   'דת ומדינה',            'Religion & State',         60),
  ('health',           'בריאות',               'Health',                   70),
  ('education',        'חינוך',                'Education',                 80),
  ('environment',      'סביבה ואקלים',         'Environment & Climate',    90),
  ('immigration',      'הגירה וקליטה',         'Immigration & Absorption', 100),
  ('civil-rights',     'זכויות אזרח',          'Civil Rights',             110),
  ('arab-society',     'החברה הערבית',         'Arab Society',             120),
  ('cost-of-living',   'יוקר המחיה',           'Cost of Living',           130),
  ('governance',       'שלטון וממשל',          'Governance & Reform',      140)
on conflict (slug) do update
  set name_he    = excluded.name_he,
      name_en    = excluded.name_en,
      sort_order = excluded.sort_order;
