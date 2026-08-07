# Frontend contribution rules

These are the invariants for the MK opinions explorer UI. They exist because the
project makes claims about what elected officials said — so the bar for evidence
and for neutral presentation is higher than in a typical web app.

See the repository root [`CONTRIBUTING.md`](../CONTRIBUTING.md) for setup, commit
conventions, and the PR process.

## Evidence rules

These are the core integrity constraints. Breaking them means the site can assert
a position an MK never took.

- Opinion evidence must come **only** from collected post data, normalized into
  `data/tweets.normalized.json`. Note that this file is generated and is not
  committed — see the root README for how to produce it.
- Never infer an opinion from party, coalition membership, biography, office, or
  outside reporting. Only what the person actually said counts.
- A missing opinion is `none`, never neutral. The absence of evidence is not
  evidence of a moderate position.
- Do not add, restore, or reference CSV data.

For every item in `data/analysis.json`:

- `status: strong` or `status: partial` requires at least one `sources` entry.
- Every source must start with `x:` and resolve to a post in
  `data/tweets.normalized.json`.
- `status: none` must have an empty source list.
- Preserve the `limitations` field — it distinguishes what is supported from what
  is not. The legacy `limit` field is accepted only during migration.

## UI constraints

- Hebrew RTL is the primary layout, not an afterthought.
- The page starts at the search and filter controls. Do not add a hero header or
  summary tiles unless explicitly requested.
- Party groups must stay collision-free and responsive.
- Names may wrap to two lines but must not overlap or escape their party island.
- MKs lacking relevant evidence should be **dimmed, not shown as holding the
  opposite view**. This one matters: styling absence as opposition would make the
  site misrepresent people.
- Keep native controls and buttons keyboard-accessible.
- Preserve the profile drawer and the source-post dialog.

## Architecture direction

- Keep source data, analysis, and UI separate.
- Keep `scripts/normalize_zip.py` deterministic.
- Add validation before changing analysis data.

## Before opening a PR

- Search the generated output for `csv:` and `sourceType": "csv"` — both must be
  absent.
- Validate every analysis source key resolves.
- Test at desktop and narrow mobile widths.
- Test topic filtering, relevant-only mode, profile opening, and post links.
- Run `npm run build` — it type-checks via `tsc -b` and must pass.
