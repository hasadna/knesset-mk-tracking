"""Backfill reviewed MK votes with visible progress and guarded BigQuery writes."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from mk_tracking.download_knesset_data.upload_over_to_bigquery import (
    DEFAULT_VOTE_CROSSWALK,
    BigQueryUploader,
    fetch_over_vote_results,
)
from mk_tracking.download_knesset_data.vote_identity import (
    load_crosswalk,
    resolve_vote_results,
)

logger = logging.getLogger("vote_backfill")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--dataset-id", default="mk_tracking")
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_VOTE_CROSSWALK)
    parser.add_argument("--start-date")
    parser.add_argument(
        "--all-reviewed",
        action="store_true",
        help="Fetch all 120 identities instead of only the 89 mismatched IDs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write through the guarded MERGE; without this flag the run is dry-only.",
    )
    args = parser.parse_args()

    crosswalk = load_crosswalk(args.crosswalk)
    logger.info("Crosswalk validated: %d reviewed MK identities", len(crosswalk))
    selected_crosswalk = (
        crosswalk
        if args.all_reviewed
        else {
            source_id: canonical_id
            for source_id, canonical_id in crosswalk.items()
            if source_id != canonical_id
        }
    )
    logger.info(
        "Backfill scope: %d %s identities",
        len(selected_crosswalk),
        "reviewed" if args.all_reviewed else "mismatched",
    )
    source_rows = fetch_over_vote_results(
        list(selected_crosswalk), start_date=args.start_date
    )
    logger.info(
        "Download complete: %s source vote results", f"{len(source_rows):,}"
    )
    resolved = resolve_vote_results(source_rows, selected_crosswalk)
    logger.info(
        "Identity resolution complete: %s/%s rows",
        f"{len(resolved):,}",
        f"{len(source_rows):,}",
    )

    uploader = BigQueryUploader(
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        dry_run=not args.apply,
    )
    prepared = uploader.load_and_merge_mk_votes(resolved)
    logger.info(
        "%s complete: %s vote rows prepared",
        "Guarded BigQuery merge" if args.apply else "Dry run",
        f"{prepared:,}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
