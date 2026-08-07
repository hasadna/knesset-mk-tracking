# MK Opinions Explorer UI

This UI is integrated into the parent `mk-tracking` Python project and built using React 19 + Vite + TypeScript. Frontend package configuration lives in `package.json` under this directory, while Python dependencies and commands are defined in the repository-root `pyproject.toml`.

A FastAPI-backed, RTL Hebrew visualization of Israeli politicians grouped by
party. Opinion evidence comes from MK posts on X, collected through the
project's ingestion pipeline and stored in BigQuery (see the repository-root
[`README.md`](../README.md)). This directory does not ship or commit any
tweet content — the interface covers **15 policy topics**, and every
non-empty opinion summary links back to source tweets served live through the
API. No CSV rows or CSV-derived conclusions are included.

## Run locally

Run the backend and UI with `uv`:

```bash
uv run mkwork
```

Open <http://127.0.0.1:8000>. Stop the server with `Ctrl+C`.

For frontend development with hot module reloading (Vite):

```bash
cd ui
npm install
npm run dev
```

Run the full evidence-integrity validation separately:

```bash
uv run mkwork validate
```

The app must be served through `mkwork` or connected to FastAPI because the browser reads roster
and evidence data through `/api`.

## Repository contents

```text
.
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── README.md
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── types.ts
│   ├── components/
│   ├── hooks/
│   ├── styles/
│   └── utils/
├── assets/
│   ├── roster.json
│   ├── party-info.json
│   ├── account-stats.json
│   └── topics.json
├── data/                    # gitignored; local snapshot for offline dev
│   ├── tweets.normalized.json
│   └── analysis.json
├── scripts/
│   └── normalize_zip.py     # builds a local snapshot from an X export
├── ../src/mk_tracking/ui_app/
│   ├── app.py
│   ├── api.py
│   ├── models.py
│   └── repositories/
├── docs/
│   ├── API_SUGGESTION.md
│   └── DATA_MODEL.md
└── tests/
    ├── test_api.py
    └── test_ui.py
```

## Product behavior

- The page starts from the search/filter controls.
- Politicians are arranged in responsive, non-overlapping **party islands**.
- Selecting a topic highlights politicians whose tweets provide evidence for
  that topic.
- Politicians without relevant evidence are dimmed, not removed by default.
- Clicking a politician opens a near-full-screen profile drawer.
- The drawer shows party information, coverage counts, topic summaries,
  limitations, and links to the source tweets.
- Portraits are loaded dynamically from Hebrew Wikipedia/Wikimedia, with
  initials as a fallback for politicians without photos.
- Interactive controls stay keyboard-accessible (native buttons/inputs, not
  div-based fakes).

## Topic taxonomy

The analysis uses these stable topic IDs:

- `regional-security` — ביטחון לאומי והמערכה האזורית
- `gaza-day-after` — עתיד רצועת עזה והיום שאחרי המלחמה
- `conflict-territories` — הסכסוך הישראלי–פלסטיני והשטחים
- `october-7` — אחריות למחדלי 7 באוקטובר
- `haredi-draft` — גיוס חרדים ושוויון בנטל
- `judiciary` — מערכת המשפט ואיזונים בין הרשויות
- `religion-state` — דת ומדינה וזהותה של ישראל
- `jewish-arab` — יחסי יהודים–ערבים ושוויון אזרחי
- `police-crime` — משטרה, פשיעה וביטחון אישי
- `cost-budget` — יוקר המחיה, מיסוי ותקציב המדינה
- `housing-transport` — דיור, תחבורה ותשתיות
- `education-employment` — חינוך, לימודי ליבה והשתלבות בתעסוקה
- `health-welfare` — בריאות, רווחה ואי־שוויון
- `governance-trust` — משילות, שחיתות ואמון במוסדות
- `media-rights` — תקשורת, חופש ביטוי וזכויות אזרח

## Evidence model

Each politician/topic record has:

```json
{
  "status": "strong | partial | none",
  "rating": "1-5 or null",
  "postCount": 12,
  "stance": "Hebrew summary grounded in the tweets",
  "extendedStance": "Optional expandable detail grounded in the tweets",
  "sources": ["x:account:tweet_id"],
  "limitations": "What cannot be concluded from the available tweets"
}
```

Rules:

1. `strong` or `partial` must have at least one valid tweet source.
2. `none` must have an empty source list.
3. Every source must resolve to a real post returned by the API (or, in
   offline mode, to `data/tweets.normalized.json`).
4. Party affiliation, biographies, or outside reporting must never be used to
   infer an opinion.
5. Missing evidence means **unknown in this dataset**, not neutral or opposed.

## API architecture

FastAPI serves the frontend through a read-only repository. BigQuery is the
default runtime backend; the JSON repository is retained as an explicit
offline fallback:

- `GET /api/health`
- `GET /api/mks`
- `GET /api/issues`
- `GET /api/mks/{mk_key}/issues`
- `GET /api/mks/{mk_key}/posts`
- `GET /api/posts/{post_key}`

The browser loads the MK list and issue taxonomy at startup. Opening a profile
requests that politician's analysis and its supporting tweets in one response;
the response is cached per politician in the browser. `postCount` is the number
of distinct posts linked to the MK and issue through `post_issue` with
`confidence >= 0.20`, unioned with every post explicitly cited in `sources`.
The threshold is applied to the seven-way softmax issue score. The resulting
count measures topic activity, while `sources` remains the smaller evidence
list used to support the generated summary. Broader ranked tweet lists remain
available through the posts endpoint, where optional issue filtering,
deduplication, and pagination happen in BigQuery.

## Known limitations

- Collected posts include retweets, quote tweets, replies, media-only posts,
  and occasionally truncated retweet text.
- Post volume is not equal across MKs.
- The dataset is a recent-window sample, not a complete political platform.
- Party affiliation and roster details depend on the completeness of the live
  `mk`, `mk_party_affiliation`, `party`, and `mk_role` tables.
- Wikipedia portraits depend on network access and page-title resolution.
