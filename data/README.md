# Hand-Crafted Seed Data (`data/automatic/`)
This directory contains certain files created by the pipeline.

# Hand-Crafted Seed Data (`data/seed/`)

This directory contains **hand-crafted, human-curated (at least human-reviewd) ground truth data** used to initialize, populate, and ground the `mk_tracking` environment.

Unlike pipeline-generated data outputs (e.g. scraped tweets, embeddings, cluster centroids, or generated crosswalks), the files in this folder are maintained by domain experts as canonical reference inputs.

## Contents

- **`issues.tsv`**: The canonical closed taxonomy of 9 policy issues, their Hebrew titles, descriptions, similarity prompts, and 1–5 rating scales (batch-loaded into `mk_tracking.issue`).
- **`issue_anchors.json`**: Multilingual reference sentences (8 per issue: Hebrew, English, Arabic) used as semantic embedding anchors for issue scoring.
- **`current_mk_roster_2026-07-31.tsv`**: Authoritative snapshot list of the 120 sitting Knesset Members and their official parties for the 25th Knesset.

## Guidelines

- **Taxonomy Control**: Automated pipelines must never invent issue slugs. New policy issues are curated here by team agreement.
- **Idempotency**: All seed loading logic uses idempotent SQL (`MERGE` or `INSERT`) so it can be safely re-executed whenever the database is rotated or rebuilt.
