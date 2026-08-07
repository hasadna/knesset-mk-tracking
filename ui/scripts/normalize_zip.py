#!/usr/bin/env python3
"""Normalize the supplied X export ZIP into data/tweets.normalized.json."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]

ACCOUNT_TO_NAME = {
    "yairlapid": "יאיר לפיד",
    "itamarbengvir": "איתמר בן גביר",
    "netanyahu": "בנימין נתניהו",
    "rothmar": "שמחה רוטמן",
    "MansourAB": "מנסור עבאס",
    "naftalibennett": "נפתלי בנט",
    "AvigdorLiberman": "אביגדור ליברמן",
    "regev_miri": "מירי רגב",
    "YairGolan1": "יאיר גולן",
    "Ahmad_tibi": "אחמד טיבי",
}


def normalize(zip_path: Path) -> list[dict]:
    records: list[dict] = []

    with zipfile.ZipFile(zip_path, "r") as archive:
        for filename in sorted(archive.namelist()):
            if not filename.endswith(".json"):
                continue

            payload = json.loads(archive.read(filename).decode("utf-8"))
            account = payload.get("account", "")
            politician = ACCOUNT_TO_NAME.get(account, account)

            for tweet in payload.get("tweets") or []:
                tweet_id = str(tweet["id"])
                created_at = tweet.get("created_at", "")
                records.append({
                    "key": f"x:{account}:{tweet_id}",
                    "sourceType": "x",
                    "publisher": politician,
                    "date": created_at[:10] if created_at else "",
                    "createdAt": created_at,
                    "text": tweet.get("text", ""),
                    "url": f"https://x.com/{account}/status/{tweet_id}",
                    "tweetId": tweet_id,
                    "account": account,
                    "lang": tweet.get("lang"),
                    "metrics": tweet.get("public_metrics", {}),
                    "referencedTweets": tweet.get("referenced_tweets", []),
                    "label": "ציוץ",
                })

    records.sort(
        key=lambda record: (record["publisher"], record.get("createdAt", "")),
        reverse=True,
    )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "zip_path",
        nargs="?",
        type=Path,
        default=UI_ROOT / "raw" / "last_100_tweets_10_mk.zip",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=UI_ROOT / "data" / "tweets.normalized.json",
    )
    args = parser.parse_args()

    records = normalize(args.zip_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(records)} tweets to {args.output}")


if __name__ == "__main__":
    main()
