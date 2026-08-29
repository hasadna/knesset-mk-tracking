import datetime
import os
from typing import Any

import httpx

X_API_BASE = "https://api.twitter.com/2"


def format_iso_timestamp(ts_str: str) -> str:
    """Ensure timestamp string is formatted as ISO 8601 UTC string (YYYY-MM-DDTHH:MM:SSZ)."""
    ts_str = ts_str.strip()
    if len(ts_str) == 10 and ts_str.count("-") == 2:
        return f"{ts_str}T00:00:00Z"
    if not ts_str.endswith("Z") and "+" not in ts_str:
        return f"{ts_str}Z"
    return ts_str


class XApiClient:
    """Client for X (Twitter) API v2."""

    def __init__(self):
        self.bearer_token = os.environ.get("X_BEARER_TOKEN", "")
        if not self.bearer_token:
            raise ValueError(
                "X_BEARER_TOKEN environment variable is required."
            )

        self.client = httpx.Client(
            base_url=X_API_BASE,
            headers={
                "Authorization": f"Bearer {self.bearer_token}",
                "User-Agent": "collect-x/0.1.0",
            },
            timeout=10.0,
        )

    def _request(self, method: str, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.request(method, endpoint, params=params)

        if response.status_code != 200:
            raise RuntimeError(
                f"X API Error ({response.status_code}): {response.text}"
            )

        return response.json()

    def get_user_data_by_username(self, username: str) -> tuple[str, dict[str, Any]]:
        """Resolve username handle to X user ID and full user object (GET /2/users/by/username/:username)."""
        clean_handle = username.lstrip("@")
        params = {
            "user.fields": "created_at,description,entities,id,location,name,pinned_tweet_id,profile_image_url,protected,public_metrics,url,username,verified,verified_type",
        }
        data = self._request("GET", f"/users/by/username/{clean_handle}", params=params)

        if "data" not in data or "id" not in data["data"]:
            raise ValueError(f"Could not resolve user ID for handle '@{clean_handle}'. API response: {data}")

        return data["data"]["id"], data

    def _extract_full_text(self, tweet: dict[str, Any], includes_tweets: dict[str, dict[str, Any]], includes_users: dict[str, dict[str, Any]]) -> str:
        """Resolve full untruncated text for long-form Note Tweets and Retweets."""
        # 1. Check if current tweet is a long-form Note Tweet
        note_tweet_text = tweet.get("note_tweet", {}).get("text")
        if note_tweet_text:
            return note_tweet_text

        # 2. Check if current tweet is a Retweet
        referenced_tweets = tweet.get("referenced_tweets", [])
        retweet_ref = next((ref for ref in referenced_tweets if ref.get("type") == "retweeted"), None)

        if retweet_ref:
            ref_id = retweet_ref.get("id")
            if ref_id and ref_id in includes_tweets:
                ref_tweet = includes_tweets[ref_id]

                # Check if referenced tweet is a long-form Note Tweet
                ref_note_text = ref_tweet.get("note_tweet", {}).get("text")
                ref_text = ref_note_text or ref_tweet.get("text", "")

                # Resolve author handle if available
                author_id = ref_tweet.get("author_id")
                author_handle = includes_users.get(author_id, {}).get("username")

                if author_handle:
                    return f"RT @{author_handle}: {ref_text}"
                return f"RT: {ref_text}"

        # 3. Standard tweet text
        return tweet.get("text", "")

    def get_tweets(
        self,
        account: str,
        start_time: str | None = None,
        end_time: str | None = None,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Fetch tweets for a specified handle or numeric user ID across optional date range (start_time to end_time) with pagination and full-text resolution."""
        raw_user_response = None
        if account.isdigit():
            user_id = account
        else:
            user_id, raw_user_response = self.get_user_data_by_username(account)

        base_params: dict[str, Any] = {
            "max_results": max(5, min(100, max_results)),
            "tweet.fields": "created_at,text,note_tweet,public_metrics,author_id,lang,conversation_id,entities,geo,in_reply_to_user_id,referenced_tweets,source",
            "expansions": "referenced_tweets.id,referenced_tweets.id.author_id",
            "user.fields": "username,name",
        }

        if start_time:
            base_params["start_time"] = format_iso_timestamp(start_time)
        if end_time:
            base_params["end_time"] = format_iso_timestamp(end_time)

        all_tweets = []
        raw_timeline_responses = []
        pagination_token = None

        while True:
            params = dict(base_params)
            if pagination_token:
                params["pagination_token"] = pagination_token

            raw_timeline_response = self._request("GET", f"/users/{user_id}/tweets", params=params)
            raw_timeline_responses.append(raw_timeline_response)

            tweets = raw_timeline_response.get("data", [])
            includes = raw_timeline_response.get("includes", {})
            includes_tweets_map = {t["id"]: t for t in includes.get("tweets", [])}
            includes_users_map = {u["id"]: u for u in includes.get("users", [])}

            for t in tweets:
                t["full_text"] = self._extract_full_text(t, includes_tweets_map, includes_users_map)

            all_tweets.extend(tweets)

            pagination_token = raw_timeline_response.get("meta", {}).get("next_token")
            if not pagination_token or (not start_time and not end_time):
                break

        return {
            "account": account,
            "user_id": user_id,
            "fetched_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "tweets_count": len(all_tweets),
            "latest_tweet": all_tweets[0] if all_tweets else None,
            "tweets": all_tweets,
            "raw_user_response": raw_user_response,
            "raw_timeline_response": raw_timeline_responses[0] if len(raw_timeline_responses) == 1 else raw_timeline_responses,
        }
