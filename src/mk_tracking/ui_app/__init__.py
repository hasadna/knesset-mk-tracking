"""Command-line entry point for validating and serving the explorer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import uvicorn

ROOT = Path(__file__).resolve().parents[3] / "ui"
EXPECTED_TWEET_COUNT = 850
VALID_STATUSES = {"strong", "partial", "none"}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{path.relative_to(ROOT)}: {error}") from error


def validate() -> list[str]:
    """Return all data-integrity errors without modifying files."""
    errors: list[str] = []
    try:
        tweets = _load_json(ROOT / "data" / "tweets.normalized.json")
        analysis = _load_json(ROOT / "data" / "analysis.json")
        roster = _load_json(ROOT / "assets" / "roster.json")
        _load_json(ROOT / "assets" / "party-info.json")
        _load_json(ROOT / "assets" / "account-stats.json")
        topic_taxonomy = _load_json(ROOT / "assets" / "topics.json")
    except ValueError as error:
        return [str(error)]

    if not isinstance(tweets, list):
        return ["data/tweets.normalized.json must contain a JSON array"]
    if len(tweets) != EXPECTED_TWEET_COUNT:
        errors.append(f"expected {EXPECTED_TWEET_COUNT} tweets, found {len(tweets)}")

    tweet_keys: set[str] = set()
    for index, tweet in enumerate(tweets):
        if not isinstance(tweet, dict):
            errors.append(f"tweet #{index} must be an object")
            continue
        key = tweet.get("key")
        if not isinstance(key, str) or not key.startswith("x:"):
            errors.append(f"tweet #{index} has invalid X source key: {key!r}")
        elif key in tweet_keys:
            errors.append(f"duplicate tweet key: {key}")
        else:
            tweet_keys.add(key)
        if tweet.get("sourceType") != "x":
            errors.append(f"{key or f'tweet #{index}'} has a non-X source type")

    if not isinstance(analysis, list):
        errors.append("data/analysis.json must contain a JSON array")
    else:
        for topic in analysis:
            topic_id = topic.get("id", "<missing>") if isinstance(topic, dict) else "<invalid>"
            members = topic.get("members") if isinstance(topic, dict) else None
            if not isinstance(members, dict):
                errors.append(f"topic {topic_id} has invalid members")
                continue
            for politician, opinion in members.items():
                label = f"{topic_id}/{politician}"
                if not isinstance(opinion, dict):
                    errors.append(f"{label} must be an object")
                    continue
                status = opinion.get("status")
                sources = opinion.get("sources")
                if status not in VALID_STATUSES:
                    errors.append(f"{label} has invalid status: {status!r}")
                if not isinstance(sources, list):
                    errors.append(f"{label} sources must be an array")
                    continue
                if status in {"strong", "partial"} and not sources:
                    errors.append(f"{label} requires at least one source")
                if status == "none" and sources:
                    errors.append(f"{label} with status none must have no sources")
                for source in sources:
                    if not isinstance(source, str) or not source.startswith("x:"):
                        errors.append(f"{label} has a non-X source: {source!r}")
                    elif source not in tweet_keys:
                        errors.append(f"{label} references unknown source: {source}")

    if not isinstance(roster, list) or not roster:
        errors.append("assets/roster.json must contain a non-empty JSON array")
    else:
        roster_keys = [member.get("key") for member in roster]
        if (
            any(not isinstance(key, str) or not key for key in roster_keys)
            or len(set(roster_keys)) != len(roster_keys)
        ):
            errors.append("every roster member must have a unique stable key")
    if not isinstance(topic_taxonomy, list) or not topic_taxonomy:
        errors.append("assets/topics.json must contain a non-empty JSON array")

    try:
        html = (ROOT / "index.html").read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"index.html: {error}")
    else:
        if "csv:" in html or 'sourceType": "csv"' in html:
            errors.append("index.html contains forbidden CSV evidence")
    return errors


def _validate_or_exit() -> None:
    errors = validate()
    if errors:
        print("Validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)
    print("Validation passed: 850 tweets and all analysis sources resolve to X data.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mk-work",
        description="Validate and serve the MK Opinions Explorer.",
    )
    parser.add_argument("command", nargs="?", choices=("serve", "validate"), default="serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "validate":
        _validate_or_exit()
        return

    print(f"Serving MK Opinions Explorer at http://{args.host}:{args.port}")
    uvicorn.run("mk_tracking.ui_app.app:app", host=args.host, port=args.port)
