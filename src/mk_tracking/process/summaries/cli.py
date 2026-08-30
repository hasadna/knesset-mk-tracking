from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from tqdm import tqdm

from .io import read_json, write_json_atomic
from .pipeline import (
    analyzable_members,
    build_prompt,
    generate,
    generate_from_inputs,
    load_inputs,
    publish,
    validate_complete,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="process-summaries")
    result.add_argument(
        "--mk-work", type=Path, default=Path("ui"), help="offline UI data root"
    )
    commands = result.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview", help="write prompts without calling GCP")
    preview.add_argument("--politician", action="append")
    preview.add_argument(
        "--output-dir", type=Path, default=Path("data/summaries/output/prompts")
    )

    create = commands.add_parser("generate", help="generate a candidate with Vertex AI")
    create.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    create.add_argument("--location", default=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    create.add_argument("--model", default="gemini-2.5-flash")
    create.add_argument("--politician", action="append")
    create.add_argument(
        "--output",
        type=Path,
        default=Path("data/summaries/output/analysis.candidate.json"),
    )
    create.add_argument(
        "--checkpoints", type=Path, default=Path("data/summaries/checkpoints")
    )

    create_db = commands.add_parser(
        "generate-db", help="read one MK from BigQuery and write a local candidate"
    )
    create_db.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    create_db.add_argument("--dataset", default="mk_tracking")
    create_db.add_argument("--location", default=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    create_db.add_argument("--model", default="gemini-2.5-flash")
    create_db.add_argument("--politician", required=True)
    create_db.add_argument(
        "--top-posts-per-issue", type=int, default=10, choices=range(1, 101)
    )
    create_db.add_argument(
        "--min-confidence", type=float, default=0.20, help="minimum similarity confidence threshold"
    )
    create_db.add_argument(
        "--output", type=Path, default=Path("data/summaries/output/db-sample.json")
    )
    create_db.add_argument(
        "--checkpoints", type=Path, default=Path("data/summaries/checkpoints")
    )
    create_db.add_argument(
        "--write-db",
        action="store_true",
        help="publish validated summaries and supporting posts to BigQuery",
    )

    check = commands.add_parser("validate", help="validate a candidate")
    check.add_argument("candidate", type=Path)

    release = commands.add_parser("publish", help="validate and copy into mk_work")
    release.add_argument("candidate", type=Path)

    run_db = commands.add_parser(
        "run-db", help="resume generation and publication for every eligible DB MK"
    )
    run_db.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    run_db.add_argument("--dataset", default="mk_tracking")
    run_db.add_argument("--location", default=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
    run_db.add_argument("--model", default="gemini-2.5-flash")
    run_db.add_argument(
        "--top-posts-per-issue", type=int, default=10, choices=range(1, 101)
    )
    run_db.add_argument(
        "--min-confidence", type=float, default=0.20, help="minimum similarity confidence threshold"
    )
    run_db.add_argument(
        "--output-dir", type=Path, default=Path("data/summaries/output/full-run")
    )
    run_db.add_argument(
        "--checkpoints", type=Path, default=Path("data/summaries/checkpoints")
    )
    return result


def main() -> None:
    args = parser().parse_args()
    root = args.mk_work.resolve()
    if args.command == "preview":
        topics, roster, posts = load_inputs(root)
        selected = set(args.politician or [])
        members = analyzable_members(roster, posts)
        if selected:
            members = [
                m
                for m in members
                if m["key"] in selected or m["name"] in selected or m["account"] in selected
            ]
        by_account = {
            member["account"]: [p for p in posts if p.get("account") == member["account"]]
            for member in members
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        manifest = []
        for member in members:
            path = args.output_dir / f"{member['key']}.txt"
            path.write_text(
                build_prompt(member, topics, by_account[member["account"]]),
                encoding="utf-8",
            )
            manifest.append({"politician": member["name"], "prompt": str(path)})
        write_json_atomic(args.output_dir / "manifest.json", manifest)
        print(f"Wrote {len(manifest)} prompt previews to {args.output_dir}")
    elif args.command == "generate":
        if not args.project:
            raise SystemExit("--project or GOOGLE_CLOUD_PROJECT is required")
        from .vertex import VertexAnalyzer

        analyzer = VertexAnalyzer(args.project, args.location, args.model)
        result = generate(
            root,
            analyzer,
            args.output,
            args.checkpoints,
            set(args.politician or []) or None,
        )
        print(f"Wrote {len(result)} topics to {args.output}")
        timing_path = args.output.with_name(f"{args.output.stem}.timings.json")
        timing = read_json(timing_path)
        for item in timing["politicians"]:
            source = "checkpoint" if item["cached"] else "Vertex AI"
            print(
                f"{item['politician']}: {item['elapsedSeconds']:.3f}s "
                f"({source})"
            )
    elif args.command == "generate-db":
        if not args.project:
            raise SystemExit("--project or GOOGLE_CLOUD_PROJECT is required")
        from .bigquery_source import BigQuerySource
        from .vertex import VertexAnalyzer

        overall_started = time.perf_counter()
        progress = tqdm(
            total=4 if args.write_db else 3,
            desc="DB summary",
            unit="stage",
            dynamic_ncols=True,
        )
        progress.set_postfix_str("loading BigQuery input")
        topics, roster, posts = BigQuerySource(args.project, args.dataset).load_inputs(
            args.politician, args.top_posts_per_issue, args.min_confidence
        )
        progress.update()
        progress.set_postfix_str(
            f"analyzing {len(posts)} posts across {len(topics)} issues"
        )
        analyzer = VertexAnalyzer(args.project, args.location, args.model)
        result = generate_from_inputs(
            topics, roster, posts, analyzer, args.output, args.checkpoints
        )
        progress.update()
        timing_path = args.output.with_name(f"{args.output.stem}.timings.json")
        timing = read_json(timing_path)["politicians"][0]
        publication = None
        if args.write_db:
            from .bigquery_sink import BigQuerySink

            progress.set_postfix_str("publishing validated result")
            publication = BigQuerySink(args.project, args.dataset).publish(
                result,
                roster[0],
                posts,
                args.model,
                timing.get("generationElapsedSeconds"),
            )
            progress.update()
        progress.set_postfix_str("complete")
        progress.update()
        progress.close()
        print(
            f"Wrote {len(result)} DB issues for {timing['politician']} "
            f"to {args.output}; total command time "
            f"{time.perf_counter() - overall_started:.3f}s"
        )
        if publication:
            print(
                f"Published run {publication['runId']}: "
                f"{publication['summaryCount']} summaries, "
                f"{publication['supportingPostCount']} supporting posts"
            )
    elif args.command == "validate":
        topics, roster, posts = load_inputs(root)
        validate_complete(read_json(args.candidate), topics, roster, posts)
        print(f"Valid: {args.candidate}")
    elif args.command == "publish":
        destination = publish(args.candidate, root)
        print(f"Published to {destination}")
    elif args.command == "run-db":
        if not args.project:
            raise SystemExit("--project or GOOGLE_CLOUD_PROJECT is required")
        from .full_run import run_full_database

        manifest = run_full_database(
            args.project,
            args.dataset,
            args.location,
            args.model,
            args.top_posts_per_issue,
            args.min_confidence,
            args.output_dir,
            args.checkpoints,
        )
        print(
            f"Full DB run: {len(manifest.get('completed', []))} completed, "
            f"{manifest.get('remainingFailures', 0)} failed, "
            f"{manifest.get('runElapsedSeconds', 0):.3f}s"
        )
        if manifest.get("remainingFailures"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
