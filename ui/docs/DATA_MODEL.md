# Data model

## `tweets.normalized.json`

One record per tweet:

- `key`: stable key in the form `x:<account>:<tweet_id>`
- `publisher`: Hebrew politician name
- `account`: X handle
- `date` / `createdAt`
- `text`
- `url`
- `metrics`
- `referencedTweets`

## `analysis.json`

An array of 15 topic objects. Each contains a `members` map keyed by
politician name. Each member record contains `status`, `stance`, optional
`extendedStance`, `sources`, and `limitations`.

## `assets/roster.json`

The visual roster and party placement. `hasData`, `postCount`, and `coverage`
are derived for the current ZIP-only snapshot.

At runtime, `/api/mks` adds a derived `category`:

- `current_mk` when `mk.is_current` is true.
- `non_mk` otherwise.

The UI groups tracked `non_mk` people together under `לא חברי כנסת`, while
retaining each person's historical `party` value for search and profiles.
Non-MKs are included when they have posts or issue summaries.

## `assets/party-info.json`

Static party seat and coalition/opposition metadata used by the presentation.
It is not an opinion source.

## `assets/account-stats.json`

Tweet counts and export metadata keyed by X account. The app uses the tweet
counts to enrich matching roster entries at startup.

## `assets/topics.json`

The non-evidentiary topic taxonomy used to build filters before analysis is
requested. It contains topic labels and axis labels, but no member opinions,
positions, sources, or tweets.

The files under `assets/` are tracked as an offline snapshot. The normal
runtime exposes roster, issue taxonomy, summaries, and posts from BigQuery
through `/api`. Analysis and normalized tweets remain ignored files under
`data/`; they are used by validation and the explicit JSON fallback, never
requested wholesale by the browser. The browser requests only the selected
politician's records and caches them per profile.
