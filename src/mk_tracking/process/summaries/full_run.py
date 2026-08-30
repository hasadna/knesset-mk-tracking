
# TODO(postgres-migration): this module still reads and/or writes BigQuery.
# BigQuery is retired as the serving layer (see src/mk_tracking/ui_app/app.py);
# PostgreSQL is the store. This path has no PostgreSQL counterpart yet, so it is
# excluded from run_daily_pipeline.sh unless MK_TRACKING_ALLOW_BIGQUERY=1.
# Porting it is the remaining work in the migration.
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tqdm import tqdm

from .bigquery_sink import BigQuerySink
from .bigquery_source import BigQuerySource
from .io import read_json, write_json_atomic
from .pipeline import generate_from_inputs
from .vertex import VertexAnalyzer


def run_full_database(
    project: str,
    dataset: str,
    location: str,
    model: str,
    top_posts_per_issue: int = 10,
    min_confidence: float = 0.20,
    output_dir: Path = Path("data/summaries/output/full-run"),
    checkpoint_dir: Path = Path("data/summaries/checkpoints"),
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest = (
        read_json(manifest_path)
        if manifest_path.exists()
        else {
            "project": project,
            "dataset": dataset,
            "model": model,
            "topPostsPerIssue": top_posts_per_issue,
            "minConfidence": min_confidence,
            "completed": [],
            "failed": [],
        }
    )
    already_completed = {
        item["mkId"] for item in manifest.get("completed", [])
    }

    source = BigQuerySource(project, dataset)
    sink = BigQuerySink(project, dataset)
    analyzer = VertexAnalyzer(project, location, model)
    eligible = [
        member
        for member in source.list_eligible_mks(skip_complete=True)
        if member["id"] not in already_completed
    ]
    failures: list[dict[str, str]] = []
    overall_started = time.perf_counter()
    progress = tqdm(
        eligible,
        desc="Full DB summaries",
        unit="MK",
        dynamic_ncols=True,
        smoothing=0.1,
    )
    for member in progress:
        progress.set_postfix_str(f"{member['name']} · loading")
        mk_started = time.perf_counter()
        try:
            topics, roster, posts = source.load_inputs(
                member["id"], top_posts_per_issue, min_confidence
            )
            safe_name = member["id"]
            candidate_path = output_dir / f"{safe_name}.json"
            progress.set_postfix_str(
                f"{member['name']} · Gemini ({len(posts)} posts)"
            )
            artifact = generate_from_inputs(
                topics,
                roster,
                posts,
                analyzer,
                candidate_path,
                checkpoint_dir,
            )
            timing_path = candidate_path.with_name(
                f"{candidate_path.stem}.timings.json"
            )
            timing = read_json(timing_path)["politicians"][0]
            progress.set_postfix_str(f"{member['name']} · publishing")
            publication = sink.publish(
                artifact,
                roster[0],
                posts,
                model,
                timing.get("generationElapsedSeconds"),
            )
            completed = {
                "mkId": member["id"],
                "name": member["name"],
                "handle": member["handle"],
                "candidate": str(candidate_path),
                "generationElapsedSeconds": timing.get(
                    "generationElapsedSeconds"
                ),
                "totalElapsedSeconds": round(
                    time.perf_counter() - mk_started, 3
                ),
                **publication,
            }
            manifest.setdefault("completed", []).append(completed)
            manifest["failed"] = [
                item
                for item in manifest.get("failed", [])
                if item.get("mkId") != member["id"]
            ]
            write_json_atomic(manifest_path, manifest)
        except Exception as error:  # noqa: BLE001 - persist per-MK failures and continue
            failure = {
                "mkId": member["id"],
                "name": member["name"],
                "error": str(error),
            }
            failures.append(failure)
            manifest["failed"] = [
                item
                for item in manifest.get("failed", [])
                if item.get("mkId") != member["id"]
            ] + [failure]
            write_json_atomic(manifest_path, manifest)
            tqdm.write(f"FAILED {member['name']}: {error}")

    manifest["runElapsedSeconds"] = round(
        time.perf_counter() - overall_started, 3
    )
    manifest["remainingFailures"] = len(failures)
    write_json_atomic(manifest_path, manifest)
    return manifest
