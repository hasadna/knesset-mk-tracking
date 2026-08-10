import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from mk_tracking.collect_x.bq_export import DEFAULT_TABLE_ID, push_tweets_to_bigquery
from mk_tracking.collect_x.x_client import XApiClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="X API Collect - Extract X (Twitter) account data cleanly."
    )
    parser.add_argument(
        "--account",
        "-a",
        type=str,
        required=True,
        help="Target X account handle (e.g. knesset_il or @knesset_il)",
    )
    parser.add_argument(
        "--since",
        "--start-time",
        type=str,
        help="Start date/timestamp for tweet extraction (e.g. 2026-07-01 or 2026-07-01T00:00:00Z)",
    )
    parser.add_argument(
        "--until",
        "--end-time",
        type=str,
        help="End date/timestamp for tweet extraction (e.g. 2026-07-30 or 2026-07-30T23:59:59Z)",
    )
    parser.add_argument(
        "--count",
        "-c",
        type=int,
        default=100,
        help="Number of tweets to fetch per timeline request (default: 100)",
    )
    parser.add_argument(
        "--push-bigquery",
        action="store_true",
        help="Push extracted tweets to Google BigQuery social_post table",
    )
    parser.add_argument(
        "--bq-table",
        type=str,
        default=DEFAULT_TABLE_ID,
        help=f"Target BigQuery table ID (default: {DEFAULT_TABLE_ID})",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data",
        help="Directory where fetched raw JSON data will be saved (default: data)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        help="Specific file path to save fetched JSON data",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Disable automatic saving of fetched data to disk",
    )
    return parser


def save_result(data: dict, output_dir: str, output_file: str | None, account: str | None) -> str:
    """Save raw data dictionary to JSON file."""
    if output_file:
        file_path = Path(output_file)
    else:
        clean_account = (account or "data").lstrip("@")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"x_data_{clean_account}_{timestamp}.json"
        file_path = Path(output_dir) / file_name

    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return str(file_path.resolve())


import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mk_tracking.collect_x.bq_export import (
    calculate_missing_date_intervals,
    get_active_mk_twitter_accounts,
    get_mk_post_date_ranges,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="X API Collect - Extract X (Twitter) account data cleanly."
    )
    parser.add_argument(
        "--account",
        "-a",
        type=str,
        help="Target X account handle (e.g. knesset_il or @knesset_il)",
    )
    parser.add_argument(
        "--all-current-mks",
        action="store_true",
        help="Query BigQuery for all current MKs' Twitter accounts and run collection for all of them",
    )
    parser.add_argument(
        "--since",
        "--start-time",
        type=str,
        help="Start date/timestamp for tweet extraction (e.g. 2026-07-01 or 2026-07-01T00:00:00Z)",
    )
    parser.add_argument(
        "--until",
        "--end-time",
        type=str,
        help="End date/timestamp for tweet extraction (e.g. 2026-07-30 or 2026-07-30T23:59:59Z)",
    )
    parser.add_argument(
        "--count",
        "-c",
        type=int,
        default=100,
        help="Number of tweets to fetch per timeline request (default: 100)",
    )
    parser.add_argument(
        "--push-bigquery",
        action="store_true",
        help="Push extracted tweets to Google BigQuery social_post table",
    )
    parser.add_argument(
        "--force-all",
        action="store_true",
        help="Force collection for all MK accounts even if posts already exist in social_post",
    )
    parser.add_argument(
        "--bq-table",
        type=str,
        default=DEFAULT_TABLE_ID,
        help=f"Target BigQuery table ID (default: {DEFAULT_TABLE_ID})",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data",
        help="Directory where fetched raw JSON data will be saved (default: data)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        help="Specific file path to save fetched JSON data",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Disable automatic saving of fetched data to disk",
    )
    return parser


def save_result(data: dict, output_dir: str, output_file: str | None, account: str | None) -> str:
    """Save raw data dictionary to JSON file."""
    if output_file:
        file_path = Path(output_file)
    else:
        clean_account = (account or "data").lstrip("@")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"x_data_{clean_account}_{timestamp}.json"
        file_path = Path(output_dir) / file_name

    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return str(file_path.resolve())


def run_cli(args: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = build_parser()
    parsed_args = parser.parse_args(args)

    if not parsed_args.account and not parsed_args.all_current_mks:
        parser.print_help()
        print("\nError: Please specify either --account <handle> or --all-current-mks.")
        return 1

    try:
        client = XApiClient()

        if parsed_args.all_current_mks:
            project_id = parsed_args.bq_table.split(".")[0] if "." in parsed_args.bq_table else os.environ.get("GOOGLE_CLOUD_PROJECT")
            if not project_id:
                print("Error: GOOGLE_CLOUD_PROJECT environment variable is not set and --bq-table does not contain a project ID.", file=sys.stderr)
                return 1
            mk_accounts = get_active_mk_twitter_accounts(project_id=project_id)

            if not mk_accounts:
                summary = {
                    "status": "warning",
                    "message": "No active current MK Twitter accounts found in BigQuery.",
                    "accounts_processed": 0,
                }
                print(json.dumps(summary, indent=2, ensure_ascii=False))
                return 0

            existing_ranges = {} if parsed_args.force_all else get_mk_post_date_ranges(table_id=parsed_args.bq_table)

            results = []
            total_tweets = 0

            for mk_acc in mk_accounts:
                handle = mk_acc["handle"]
                mk_id_uuid = mk_acc["mk_id"]
                account_id_uuid = mk_acc["account_id"]

                existing_range = existing_ranges.get(mk_id_uuid)
                missing_intervals = calculate_missing_date_intervals(
                    parsed_args.since, parsed_args.until, existing_range
                )

                if not missing_intervals:
                    sys.stderr.write(f"Skip @{handle}: requested range already covered by BigQuery data.\n")
                    results.append({
                        "handle": handle,
                        "mk_id": mk_id_uuid,
                        "tweets_count": 0,
                        "status": "skipped_already_covered",
                    })
                    continue

                account_tweets = 0
                for sub_start, sub_end in missing_intervals:
                    try:
                        res = client.get_tweets(
                            account=handle,
                            start_time=sub_start,
                            end_time=sub_end,
                            max_results=parsed_args.count,
                        )

                        if parsed_args.push_bigquery:
                            bq_res = push_tweets_to_bigquery(
                                result_data=res,
                                table_id=parsed_args.bq_table,
                                mk_id_uuid=mk_id_uuid,
                                account_id_uuid=account_id_uuid,
                            )
                            res["bigquery_export"] = bq_res

                        if not parsed_args.no_save:
                            saved_path = save_result(
                                data=res,
                                output_dir=parsed_args.output_dir,
                                output_file=None,
                                account=handle,
                            )
                            res["saved_to"] = saved_path

                        account_tweets += res.get("tweets_count", 0)

                    except Exception as acc_err:
                        sys.stderr.write(f"Warning: Failed interval [{sub_start}..{sub_end}] for @{handle}: {acc_err}\n")

                results.append({
                    "handle": handle,
                    "mk_id": mk_id_uuid,
                    "tweets_count": account_tweets,
                    "bigquery_status": "success",
                })
                total_tweets += account_tweets

            overall_summary = {
                "status": "success",
                "accounts_processed": len(results),
                "total_tweets_collected": total_tweets,
                "account_summaries": results,
            }
            print(json.dumps(overall_summary, indent=2, ensure_ascii=False))
            return 0

        else:
            result = client.get_tweets(
                account=parsed_args.account,
                start_time=parsed_args.since,
                end_time=parsed_args.until,
                max_results=parsed_args.count,
            )

            if parsed_args.push_bigquery:
                bq_result = push_tweets_to_bigquery(
                    result_data=result,
                    table_id=parsed_args.bq_table,
                )
                result["bigquery_export"] = bq_result

            if not parsed_args.no_save and parsed_args.account:
                saved_path = save_result(
                    data=result,
                    output_dir=parsed_args.output_dir,
                    output_file=parsed_args.output_file,
                    account=parsed_args.account,
                )
                result["saved_to"] = saved_path

            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

    except Exception as err:
        error_output = {
            "error": str(err),
            "status": "failed",
        }
        print(json.dumps(error_output, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1


main = run_cli

if __name__ == "__main__":
    sys.exit(run_cli())
