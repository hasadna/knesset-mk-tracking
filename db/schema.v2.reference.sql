-- ============================================================================
-- mk-tracking — database schema (PostgreSQL / Supabase) — v2 REFERENCE ONLY
-- ⚠️  NOT the deployed schema. Deployed = BigQuery v3 (db/schema.bq.sql); this
-- file was not updated for the v3 spec change (see docs/DATABASE_DESIGN.md §10).
-- ============================================================================
-- See docs/DATABASE_DESIGN.md for the narrative design and rationale.
--
-- Apply with:  psql "$DATABASE_URL" -f db/schema.sql
-- or as a Supabase migration (supabase db push).
-- Requires PostgreSQL 15+ (uses UNIQUE NULLS NOT DISTINCT, security_invoker views).
--
-- Design notes:
--  * bigint identity PKs for internal FKs; MKs also carry a URL `slug`.
--  * Enums used for stable, closed value sets (platform, language, stance, ...).
--    Extend with:  ALTER TYPE <name> ADD VALUE '<new>';
--  * "Current" for time-bounded rows (affiliation, role, account) means
--    `end_date is null` / `is_active` — there is deliberately no separate
--    is_current flag to drift out of sync.
--  * All ingestion writes are idempotent upserts on natural keys (see the
--    ingestion contract in the design doc). quote dedups on a stored text hash.
--  * Every serving table is public-read via RLS; writes are service-role only
--    (the sentiment/ingestion team). See the RLS section at the bottom.
-- ============================================================================

create extension if not exists pg_trgm;  -- substring/keyword search over Hebrew text

-- ---------------------------------------------------------------------------
-- Enum types
-- ---------------------------------------------------------------------------
create type platform      as enum ('twitter', 'facebook', 'instagram', 'telegram', 'tiktok', 'gov_il', 'other');
create type content_lang  as enum ('he', 'en', 'ar', 'other');
create type stance        as enum ('supports', 'opposes', 'mixed', 'unclear');
create type quote_source  as enum ('social_post', 'knesset_speech', 'interview', 'other');
create type role_type     as enum (
  'prime_minister', 'minister', 'deputy_minister',
  'knesset_speaker', 'deputy_speaker',
  'committee_chair', 'committee_member',
  'faction_chair', 'coalition', 'opposition', 'other'
);
create type vote_value    as enum ('for', 'against', 'abstain', 'absent');
create type relation_kind as enum ('same_party', 'similar_positions', 'notable', 'ally', 'rival');
create type issue_signal  as enum ('support', 'oppose', 'neutral', 'mixed');

-- ---------------------------------------------------------------------------
-- Housekeeping trigger: keep updated_at honest
-- ---------------------------------------------------------------------------
create function touch_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- ---------------------------------------------------------------------------
-- Reference / dimension tables
-- ---------------------------------------------------------------------------

create table party (
  id          bigint generated always as identity primary key,
  name_he     text not null,
  name_en     text,
  short_name  text,
  is_current  boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create trigger party_touch before update on party
  for each row execute function touch_updated_at();

create table committee (
  id                   bigint generated always as identity primary key,
  knesset_committee_id integer unique,          -- external Open Knesset / OData id
  name_he              text not null,
  name_en              text
);

-- Predetermined policy / issue areas. Seeded once (see db/seed.sql), referenced everywhere.
create table issue (
  id             bigint generated always as identity primary key,
  slug           text unique not null,          -- stable machine key, e.g. 'housing'
  name_he        text not null,
  name_en        text,
  description_he text,
  description_en text,
  sort_order     integer not null default 0
);

-- ---------------------------------------------------------------------------
-- Member of Knesset (MK) and time-bounded affiliations / roles
-- ---------------------------------------------------------------------------

create table mk (
  id                bigint generated always as identity primary key,
  knesset_member_id integer unique,             -- external hook to Open Knesset / OData / votes
  slug              text unique not null,        -- URL key, e.g. 'yair-lapid'
  full_name_he      text not null,
  full_name_en      text,
  photo_url         text,
  birth_date        date,
  gender            text,
  home_city         text,
  bio_he            text,
  bio_en            text,
  is_current        boolean not null default true,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create trigger mk_touch before update on mk
  for each row execute function touch_updated_at();

-- Party membership over time. Seed the current row now; back-fill history later
-- with no schema change. end_date null == currently in this party.
create table mk_affiliation (
  id         bigint generated always as identity primary key,
  mk_id      bigint not null references mk(id) on delete cascade,
  party_id   bigint not null references party(id),
  start_date date,
  end_date   date                                 -- null == current
);

-- Offices and parliamentary roles over time (minister, committee chair, coalition/opposition, ...).
create table mk_role (
  id           bigint generated always as identity primary key,
  mk_id        bigint not null references mk(id) on delete cascade,
  role_type    role_type not null,
  title_he     text,                             -- free text, e.g. 'שר האוצר'
  title_en     text,
  committee_id bigint references committee(id),  -- when the role is committee-scoped
  start_date   date,
  end_date     date                               -- null == current
);

-- Registry of MK social accounts (seeded from the account list, independent of
-- whether posts were collected yet). Drives the "platforms used" filter and
-- tells the ingestion pipeline where to look.
create table mk_social_account (
  id         bigint generated always as identity primary key,
  mk_id      bigint not null references mk(id) on delete cascade,
  platform   platform not null,
  handle     text,                                -- e.g. 'yairlapid'
  url        text,
  is_active  boolean not null default true,       -- account closed/suspended => false
  verified   boolean not null default false,      -- did a human confirm this is really the MK
  unique (mk_id, platform, handle)
);

-- ---------------------------------------------------------------------------
-- Raw social media posts (the evidence quotes are extracted from)
-- ---------------------------------------------------------------------------

create table social_post (
  id               bigint generated always as identity primary key,
  mk_id            bigint not null references mk(id) on delete cascade,
  account_id       bigint references mk_social_account(id),
  platform         platform not null,
  platform_post_id text,                          -- native id on the platform
  url              text,
  posted_at        timestamptz,
  text             text,
  language         content_lang,
  engagement       jsonb not null default '{}',   -- {likes, shares, comments, views, ...}
  is_deleted       boolean not null default false,-- deleted-post tracking (politwoops-style)
  fetched_at       timestamptz,
  created_at       timestamptz not null default now(),
  unique (platform, platform_post_id)
);

-- ---------------------------------------------------------------------------
-- Quotes / statements  (the taggable unit the sentiment team produces)
-- ---------------------------------------------------------------------------

create table quote (
  id                  bigint generated always as identity primary key,
  mk_id               bigint not null references mk(id) on delete cascade,  -- denormalized for fast per-MK reads
  source_post_id      bigint references social_post(id) on delete set null, -- null if from a speech/interview
  source_type         quote_source not null default 'social_post',
  text                text not null,
  text_hash           text generated always as (md5(text)) stored,  -- dedup key for idempotent re-ingestion
  language            content_lang not null,       -- per-quote he/en/ar/other tag + filter
  said_at             timestamptz,                 -- when stated; primary sort key on the timeline
  is_concrete_promise boolean not null default false, -- the true/false policy-declaration flag
  context             text,
  model_version       text,                        -- provenance: which pipeline/model tagged this
  created_at          timestamptz not null default now(),
  -- Idempotency: re-running the pipeline upserts instead of duplicating.
  -- NULLS NOT DISTINCT so speech/interview quotes (null source_post_id) dedup too.
  constraint quote_dedup unique nulls not distinct (mk_id, source_post_id, text_hash)
);

-- Guard the denormalized mk_id: a quote must belong to the same MK as its source post.
create function check_quote_mk() returns trigger language plpgsql as $$
begin
  if new.source_post_id is not null and
     (select mk_id from social_post where id = new.source_post_id) is distinct from new.mk_id then
    raise exception 'quote.mk_id % does not match mk_id of source_post %', new.mk_id, new.source_post_id;
  end if;
  return new;
end $$;
create trigger quote_mk_guard before insert or update on quote
  for each row execute function check_quote_mk();

-- A quote can touch several issues; each link carries the sentiment on that issue.
create table quote_issue (
  id          bigint generated always as identity primary key,
  quote_id    bigint not null references quote(id) on delete cascade,
  issue_id    bigint not null references issue(id),
  signal      issue_signal not null default 'neutral',
  valence     numeric(4,3) check (valence between -1 and 1),
  confidence  numeric(4,3) check (confidence between 0 and 1),
  unique (quote_id, issue_id)
);

-- ---------------------------------------------------------------------------
-- Per-(MK, issue) position summary — the rollup the profile UI shows
-- ---------------------------------------------------------------------------

create table mk_issue_position (
  id                   bigint generated always as identity primary key,
  mk_id                bigint not null references mk(id) on delete cascade,
  issue_id             bigint not null references issue(id),
  stance               stance not null default 'unclear',
  -- Consistency is a separate axis from stance: an MK can currently support
  -- something AND have flip-flopped to get there. Filterable independently.
  is_flip_flop         boolean not null default false,
  position_summary_he  text,                       -- short summary; may describe the flip-flop
  position_summary_en  text,
  overall_valence      numeric(4,3) check (overall_valence between -1 and 1),
  has_concrete_promise boolean not null default false,
  model_version        text,
  updated_at           timestamptz not null default now(),
  unique (mk_id, issue_id)
);
create trigger mk_issue_position_touch before update on mk_issue_position
  for each row execute function touch_updated_at();

-- ---------------------------------------------------------------------------
-- Parliamentary activity — bills and votes ("said vs did" crossing)
-- ---------------------------------------------------------------------------

create table bill (
  id              bigint generated always as identity primary key,
  knesset_bill_id integer unique,                  -- external id from Open Knesset / OData
  title_he        text not null,
  title_en        text,
  summary         text,
  status          text,
  created_at      timestamptz not null default now()
);

create table bill_issue (
  bill_id  bigint not null references bill(id) on delete cascade,
  issue_id bigint not null references issue(id),
  primary key (bill_id, issue_id)
);

-- One row per MK per voting event. `reading` distinguishes the multiple votes
-- a bill goes through (1st/2nd/3rd reading, continuity, ...); null when unknown.
create table mk_vote (
  id        bigint generated always as identity primary key,
  mk_id     bigint not null references mk(id) on delete cascade,
  bill_id   bigint not null references bill(id) on delete cascade,
  reading   smallint,
  vote      vote_value not null,
  voted_at  timestamptz,
  constraint mk_vote_event unique nulls not distinct (mk_id, bill_id, reading)
);

-- ---------------------------------------------------------------------------
-- Connections between MKs (party grouping, similar-position edges, "notable")
-- ---------------------------------------------------------------------------

create table mk_relation (
  id             bigint generated always as identity primary key,
  mk_id          bigint not null references mk(id) on delete cascade,
  related_mk_id  bigint not null references mk(id) on delete cascade,
  relation_type  relation_kind not null,
  issue_id       bigint references issue(id),       -- set when relation is issue-specific
  score          numeric(4,3) check (score between -1 and 1),
  check (mk_id <> related_mk_id),
  constraint mk_relation_edge unique nulls not distinct (mk_id, related_mk_id, relation_type, issue_id)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
create index on mk (is_current);
create index on mk_affiliation (mk_id) where end_date is null;   -- current-party lookups
create index on mk_affiliation (party_id);
create index on mk_role (mk_id) where end_date is null;          -- current-roles lookups
create index on mk_social_account (mk_id);
create index on social_post (mk_id, posted_at desc);
create index on social_post (platform);
create index on quote (mk_id, said_at desc);                     -- the profile timeline
create index on quote (mk_id) where is_concrete_promise;         -- promises filter
create index on quote (language);
create index on quote using gin (text gin_trgm_ops);             -- keyword search (Hebrew-safe)
create index on social_post using gin (text gin_trgm_ops);
create index on quote_issue (issue_id, quote_id);
create index on mk_issue_position (issue_id);
create index on mk_vote (mk_id);
create index on mk_vote (bill_id);
create index on bill_issue (issue_id);
create index on mk_relation (mk_id);

-- ---------------------------------------------------------------------------
-- Views for the application (read models)
-- ---------------------------------------------------------------------------
-- security_invoker: run with the caller's permissions so RLS on the underlying
-- tables applies (all public-read today, but keeps the model honest).

-- Main-page grid: one card per MK, with current party, roles, platforms, issue coverage.
-- Platforms come from the account registry, not collected posts, so the filter
-- works before/without ingestion.
create view v_mk_card with (security_invoker = true) as
select
  m.id, m.slug, m.full_name_he, m.full_name_en, m.photo_url, m.is_current,
  p.name_he  as party_he,
  p.name_en  as party_en,
  (select array_agg(distinct sa.platform) from mk_social_account sa
    where sa.mk_id = m.id and sa.is_active)                              as platforms,
  (select array_agg(distinct r.role_type) from mk_role r
    where r.mk_id = m.id and r.end_date is null)                         as current_roles,
  (select count(distinct ip.issue_id) from mk_issue_position ip
    where ip.mk_id = m.id)                                               as issue_count
from mk m
left join mk_affiliation a on a.mk_id = m.id and a.end_date is null
left join party p on p.id = a.party_id;

-- Per-(MK, issue) position with quote counts — the profile's issue blocks.
create view v_mk_issue_position with (security_invoker = true) as
select
  ip.mk_id, ip.issue_id, i.slug as issue_slug, i.name_he as issue_he, i.name_en as issue_en,
  ip.stance, ip.is_flip_flop, ip.position_summary_he, ip.position_summary_en,
  ip.overall_valence, ip.has_concrete_promise, ip.updated_at,
  (select count(*) from quote q
     join quote_issue qi on qi.quote_id = q.id
    where q.mk_id = ip.mk_id and qi.issue_id = ip.issue_id) as quote_count,
  (select count(*) from quote q
     join quote_issue qi on qi.quote_id = q.id
    where q.mk_id = ip.mk_id and qi.issue_id = ip.issue_id and q.is_concrete_promise) as promise_count
from mk_issue_position ip
join issue i on i.id = ip.issue_id;

-- "Matrix transpose": for each issue, every MK's stance — powers the cross-MK issue view.
create view v_issue_landscape with (security_invoker = true) as
select
  i.id as issue_id, i.slug as issue_slug, i.name_he as issue_he,
  ip.mk_id, m.full_name_he, m.slug as mk_slug,
  ip.stance, ip.is_flip_flop, ip.overall_valence, ip.has_concrete_promise
from issue i
join mk_issue_position ip on ip.issue_id = i.id
join mk m on m.id = ip.mk_id;

-- "Said vs did": stated stance per issue next to the MK's vote record on bills tagged to that issue.
create view v_said_vs_did with (security_invoker = true) as
select
  ip.mk_id, ip.issue_id, ip.stance as stated_stance,
  count(*) filter (where v.vote = 'for')     as votes_for,
  count(*) filter (where v.vote = 'against') as votes_against,
  count(*) filter (where v.vote = 'abstain') as votes_abstain
from mk_issue_position ip
join bill_issue bi on bi.issue_id = ip.issue_id
join mk_vote v on v.bill_id = bi.bill_id and v.mk_id = ip.mk_id
group by ip.mk_id, ip.issue_id, ip.stance;

-- ---------------------------------------------------------------------------
-- RPC: compound quote search in one round-trip
-- ---------------------------------------------------------------------------
-- The profile page's filtered quote list (issue + platform + language +
-- promises-only + free-text keyword), server-side. Call via
-- supabase.rpc('search_quotes', {...}); all filters optional.
create function search_quotes(
  p_mk_id         bigint       default null,
  p_issue_slug    text         default null,
  p_platform      platform     default null,
  p_language      content_lang default null,
  p_promises_only boolean      default false,
  p_keyword       text         default null,
  p_limit         integer      default 50,
  p_offset        integer      default 0
) returns table (
  quote_id            bigint,
  mk_id               bigint,
  text                text,
  language            content_lang,
  said_at             timestamptz,
  is_concrete_promise boolean,
  issue_slug          text,
  signal              issue_signal,
  valence             numeric,
  post_platform       platform,
  post_url            text
) language sql stable as $$
  select
    q.id, q.mk_id, q.text, q.language, q.said_at, q.is_concrete_promise,
    i.slug, qi.signal, qi.valence, sp.platform, sp.url
  from quote q
  join quote_issue qi on qi.quote_id = q.id
  join issue i        on i.id = qi.issue_id
  left join social_post sp on sp.id = q.source_post_id
  where (p_mk_id      is null or q.mk_id = p_mk_id)
    and (p_issue_slug is null or i.slug = p_issue_slug)
    and (p_platform   is null or sp.platform = p_platform)
    and (p_language   is null or q.language = p_language)
    and (not p_promises_only or q.is_concrete_promise)
    and (p_keyword    is null or q.text ilike '%' || p_keyword || '%')
  order by q.said_at desc nulls last
  limit p_limit offset p_offset
$$;

-- ---------------------------------------------------------------------------
-- Row Level Security — public read, service-role write
-- ---------------------------------------------------------------------------
-- Enable RLS and grant SELECT to the anon/authenticated roles on every table.
-- Writes carry no policy, so only the service_role key (used by the ingestion
-- pipeline) bypasses RLS and can insert/update.
do $$
declare t text;
begin
  foreach t in array array[
    'party','committee','issue','mk','mk_affiliation','mk_role','mk_social_account',
    'social_post','quote','quote_issue','mk_issue_position','bill','bill_issue',
    'mk_vote','mk_relation'
  ] loop
    execute format('alter table %I enable row level security;', t);
    execute format($p$create policy %I on %I for select to anon, authenticated using (true);$p$,
                   'public_read_' || t, t);
  end loop;
end $$;
