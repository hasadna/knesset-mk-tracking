"""Command-line interface for the embedding pipeline."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from mk_tracking.pipeline import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    VertexAIService,
    embed_subjects,
    embed_tweets,
    transform_tweet_exports,
)

DEFAULT_RAW_TWEETS = Path("data/last_100_tweets_10_mk")
DEFAULT_SUBJECTS = Path("data/seed/subjects.json")
DEFAULT_TWEET_TABLE = Path("data/processed/tweets.jsonl")
DEFAULT_EMBEDDED_TWEETS = Path("data/processed/tweets_with_embeddings.jsonl")
DEFAULT_EMBEDDED_SUBJECTS = Path("data/processed/subjects_with_embeddings.jsonl")


def _print_progress(message: str) -> None:
    print(message, flush=True)


def _add_vertex_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP project ID (defaults to GOOGLE_CLOUD_PROJECT)",
    )
    parser.add_argument("--location", default="global")
    parser.add_argument(
        "--dimensions",
        type=int,
        default=DEFAULT_EMBEDDING_DIMENSIONS,
    )
    parser.add_argument("--max-retries", type=int, default=6)


def _service(args: argparse.Namespace) -> VertexAIService:
    return VertexAIService(
        project=args.project,
        location=args.location,
        output_dimensions=args.dimensions,
        max_retries=args.max_retries,
        progress=_print_progress,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mk-tracking",
        description="Transform and embed MK tweets and political subjects.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    transform = commands.add_parser("transform", help="JSON exports to tweet table")
    transform.add_argument("--input", type=Path, default=DEFAULT_RAW_TWEETS)
    transform.add_argument("--output", type=Path, default=DEFAULT_TWEET_TABLE)

    tweet_embeddings = commands.add_parser(
        "embed-tweets",
        help="Add Gemini embeddings to the tweet table",
    )
    tweet_embeddings.add_argument("--input", type=Path, default=DEFAULT_TWEET_TABLE)
    tweet_embeddings.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_EMBEDDED_TWEETS,
    )
    _add_vertex_arguments(tweet_embeddings)

    subject_embeddings = commands.add_parser(
        "embed-subjects",
        help="Embed curated subject and representative-sentence anchors",
    )
    subject_embeddings.add_argument("--input", type=Path, default=DEFAULT_SUBJECTS)
    subject_embeddings.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_EMBEDDED_SUBJECTS,
    )
    _add_vertex_arguments(subject_embeddings)

    run = commands.add_parser("run", help="Run all three stages")
    run.add_argument("--tweets", type=Path, default=DEFAULT_RAW_TWEETS)
    run.add_argument("--subjects", type=Path, default=DEFAULT_SUBJECTS)
    run.add_argument("--output-directory", type=Path, default=Path("data/processed"))
    _add_vertex_arguments(run)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "transform":
        _print_progress(f"Transforming raw tweets from {args.input}")
        path = transform_tweet_exports(args.input, args.output)
        _print_progress(f"Tweet table saved to {path}")
        return

    service = _service(args)
    if args.command == "embed-tweets":
        path = embed_tweets(
            args.input,
            args.output,
            service=service,
            progress=_print_progress,
        )
        _print_progress(f"Embedded tweet table ready at {path}")
        return
    if args.command == "embed-subjects":
        path = embed_subjects(
            args.input,
            args.output,
            service=service,
            progress=_print_progress,
        )
        _print_progress(f"Embedded subject table ready at {path}")
        return

    output_directory: Path = args.output_directory
    _print_progress("[1/3] Transforming raw tweet exports")
    tweet_table = transform_tweet_exports(
        args.tweets,
        output_directory / "tweets.jsonl",
    )
    _print_progress(f"[1/3] Tweet table ready at {tweet_table}")
    _print_progress("[2/3] Embedding tweets")
    embedded_tweets = embed_tweets(
        tweet_table,
        output_directory / "tweets_with_embeddings.jsonl",
        service=service,
        progress=_print_progress,
    )
    _print_progress(f"[2/3] Embedded tweet table ready at {embedded_tweets}")
    _print_progress("[3/3] Embedding subject anchors")
    embedded_subjects = embed_subjects(
        args.subjects,
        output_directory / "subjects_with_embeddings.jsonl",
        service=service,
        progress=_print_progress,
    )
    _print_progress(f"[3/3] Embedded subject table ready at {embedded_subjects}")


if __name__ == "__main__":
    main()
