"""Incrementally embed BigQuery tweets and infer K-means cluster meanings.

Outputs are restart-friendly:

* ``tweet_embeddings.npz`` stores only ``tweet_id`` and ``embedding`` arrays.
* ``tweet_cluster_centroids.npz`` stores centroids, Hebrew meanings, and
  Gemini-generated garbage-cluster decisions.
* ``issue_anchor_embeddings.npz`` stores the curated multilingual issue anchors.
* ``big_query_to_data_run_history.jsonl`` records work and Gemini API cost.
"""

# TODO(postgres-migration): this module still reads and/or writes BigQuery.
# BigQuery is retired as the serving layer (see src/mk_tracking/ui_app/app.py);
# PostgreSQL is the store. This path has no PostgreSQL counterpart yet, so it is
# excluded from run_daily_pipeline.sh unless MK_TRACKING_ALLOW_BIGQUERY=1.
# Porting it is the remaining work in the migration.

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from google.api_core.exceptions import Forbidden
from google.cloud import bigquery
from sklearn.cluster import KMeans
from tqdm.auto import tqdm

from mk_tracking.bigquery_issue_scoring import (
    DEFAULT_SOFTMAX_TEMPERATURE,
    compute_missing_issue_scores,
    fetch_existing_post_issue_pairs,
    fetch_issue_anchors,
    fetch_issues,
    initialize_anchor_embeddings,
    issue_score_model_version,
    load_anchor_definitions,
    sync_missing_tweet_embeddings,
    upload_issue_anchors,
    upload_issue_scores,
)
from mk_tracking.pipeline import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    GeneratedText,
    VertexAIService,
)

DEFAULT_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
DEFAULT_DATASET = "mk_tracking"
DEFAULT_TABLE = "social_post"
DEFAULT_BIGQUERY_LOCATION = "US"
DEFAULT_VERTEX_LOCATION = "global"
SUMMARY_MODEL = "gemini-2.5-flash"
DEFAULT_K = 30
DEFAULT_SAMPLE_N = 100
DEFAULT_RANDOM_STATE = 42
DEFAULT_N_INIT = 1
DEFAULT_CHECKPOINT_EVERY = 25

EMBEDDING_INPUT_USD_PER_MILLION_TOKENS = Decimal("0.15")
FLASH_INPUT_USD_PER_MILLION_TOKENS = Decimal("0.30")
FLASH_OUTPUT_USD_PER_MILLION_TOKENS = Decimal("2.50")
ONE_MILLION = Decimal(1000000)
PRICING_SOURCE = (
    "https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing"
)

DEFAULT_EMBEDDING_FILE = Path("data/processed/tweet_embeddings.npz")
DEFAULT_CENTROID_FILE = Path("data/processed/tweet_cluster_centroids.npz")
DEFAULT_RUN_HISTORY_FILE = Path("data/processed/big_query_to_data_run_history.jsonl")
DEFAULT_ANCHOR_DEFINITIONS_FILE = Path("db/issue_anchors.json")
DEFAULT_ANCHOR_EMBEDDING_FILE = Path("data/processed/issue_anchor_embeddings.npz")
DEFAULT_CLUSTER_FLAGS_FILE = Path("db/tweet_cluster_flags.json")

_PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Tweet:
    tweet_id: str
    text: str
    needs_db_embedding: bool = True


@dataclass(frozen=True)
class ClusterFlags:
    model_version: str
    embedding_model: str
    summary_model: str
    k: int
    random_state: int
    n_init: int
    garbage_cluster_ids: frozenset[int]


@dataclass(frozen=True)
class ClusterStore:
    centroids: np.ndarray
    titles_he: np.ndarray
    descriptions_he: np.ndarray
    cluster_sizes: np.ndarray
    garbage_reasons_he: np.ndarray
    flags: ClusterFlags


@dataclass
class ApiUsage:
    embedding_requests: int = 0
    embedding_input_tokens: int = 0
    summarization_requests: int = 0
    summarization_input_tokens: int = 0
    summarization_output_tokens: int = 0


@dataclass
class BigQueryUsage:
    rows: int = 0
    total_bytes_processed: int = 0
    total_bytes_billed: int = 0
    cache_hit: bool | None = None


@dataclass
class RunState:
    started_at: str
    status: str = "running"
    finished_at: str | None = None
    source_tweets: int = 0
    embeddings_before: int = 0
    tweets_processed: int = 0
    embeddings_after: int = 0
    db_embeddings_backfilled: int = 0
    anchors_embedded: int = 0
    anchors_uploaded: int = 0
    issue_score_rows_computed: int = 0
    issue_score_rows_inserted: int = 0
    clusters_uploaded: int = 0
    garbage_clusters: int = 0
    centroids_updated: bool = False
    api_usage: ApiUsage = field(default_factory=ApiUsage)
    bigquery: BigQueryUsage = field(default_factory=BigQueryUsage)
    error: str | None = None


class AIService(Protocol):
    embedding_model: str
    output_dimensions: int
    last_embedding_input_tokens: int | None

    def embed_text(self, text: str, *, role: str) -> list[float]:
        """Return one normalized embedding."""

    def generate_text(
        self,
        prompt: str,
        *,
        model: str,
        max_output_tokens: int,
        temperature: float,
        thinking_budget: int | None,
        response_mime_type: str | None,
        response_schema: dict[str, Any] | None,
    ) -> GeneratedText:
        """Generate text and return its billable token counts."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _validate_project(project: str) -> str:
    if not _PROJECT_PATTERN.fullmatch(project):
        raise ValueError(f"Invalid GCP project ID: {project!r}")
    return project


def _validate_identifier(value: str, *, label: str) -> str:
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid BigQuery {label}: {value!r}")
    return value


def fetch_tweets(
    *,
    project: str,
    dataset: str,
    table: str,
    location: str,
    client: bigquery.Client | None = None,
) -> tuple[list[Tweet], BigQueryUsage]:
    """Read all non-empty tweet texts from BigQuery in stable ID order."""

    project = _validate_project(project)
    dataset = _validate_identifier(dataset, label="dataset")
    table = _validate_identifier(table, label="table")
    full_table_id = f"{project}.{dataset}.{table}"
    query = (
        f"SELECT id, text, ARRAY_LENGTH(embedding) AS embedding_length "
        f"FROM `{full_table_id}` "
        "WHERE text IS NOT NULL AND TRIM(text) != '' ORDER BY id"
    )
    query_client = client or bigquery.Client(
        project=project,
        location=location,
    )
    print(f"Reading tweets from {full_table_id}", flush=True)
    try:
        job = query_client.query(
            query,
            location=location,
            job_config=bigquery.QueryJobConfig(use_query_cache=True),
        )
        rows = job.result()
    except Forbidden as error:
        raise PermissionError(
            "BigQuery access denied. The ADC identity needs BigQuery Data "
            "Viewer and BigQuery Job User permissions."
        ) from error

    tweets: list[Tweet] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(
        tqdm(
            rows,
            total=getattr(rows, "total_rows", None) or len(rows),
            desc="Reading post metadata",
            unit="post",
            dynamic_ncols=True,
        ),
        start=1,
    ):
        tweet_id = str(row["id"]).strip()
        text = str(row["text"]).strip()
        if not tweet_id or not text:
            raise ValueError(f"BigQuery returned an invalid row at position {index}")
        if tweet_id in seen_ids:
            raise ValueError(f"BigQuery returned duplicate tweet ID {tweet_id!r}")
        seen_ids.add(tweet_id)
        try:
            embedding_length = int(row["embedding_length"] or 0)
        except (KeyError, TypeError, ValueError):
            embedding_length = 0
        if embedding_length not in {0, DEFAULT_EMBEDDING_DIMENSIONS}:
            raise ValueError(
                f"BigQuery tweet {tweet_id!r} has invalid embedding length "
                f"{embedding_length}"
            )
        tweets.append(
            Tweet(
                tweet_id=tweet_id,
                text=text,
                needs_db_embedding=embedding_length == 0,
            )
        )

    usage = BigQueryUsage(
        rows=len(tweets),
        total_bytes_processed=int(job.total_bytes_processed or 0),
        total_bytes_billed=int(job.total_bytes_billed or 0),
        cache_hit=job.cache_hit,
    )
    print(f"Loaded {len(tweets):,} tweets from BigQuery", flush=True)
    return tweets, usage


def load_embedding_store(
    path: Path,
    *,
    dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
) -> tuple[np.ndarray, np.ndarray]:
    """Load and validate the compact ``tweet_id,embedding`` NPZ file."""

    if not path.exists():
        return (
            np.asarray([], dtype=np.str_),
            np.empty((0, dimensions), dtype=np.float32),
        )
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"tweet_id", "embedding"}:
            raise ValueError(
                f"{path} must contain only 'tweet_id' and 'embedding' arrays"
            )
        tweet_ids = np.asarray(data["tweet_id"], dtype=np.str_)
        embeddings = np.asarray(data["embedding"], dtype=np.float32)

    if tweet_ids.ndim != 1:
        raise ValueError(f"{path}: tweet_id must be a one-dimensional array")
    if embeddings.shape != (len(tweet_ids), dimensions):
        raise ValueError(
            f"{path}: embedding shape is {embeddings.shape}; expected "
            f"({len(tweet_ids)}, {dimensions})"
        )
    if len(set(tweet_ids.tolist())) != len(tweet_ids):
        raise ValueError(f"{path}: tweet_id contains duplicates")
    if not np.isfinite(embeddings).all():
        raise ValueError(f"{path}: embedding contains non-finite values")
    return tweet_ids, embeddings


def load_cluster_flags(path: Path) -> ClusterFlags:
    """Load the manually reviewed cluster configuration and garbage flags."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read cluster flags from {path}") from error
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError(f"{path} must contain cluster flag schema version 1")

    model_version = str(payload.get("model_version") or "").strip()
    embedding_model = str(payload.get("embedding_model") or "").strip()
    summary_model = str(payload.get("summary_model") or "").strip()
    k = payload.get("k")
    random_state = payload.get("random_state")
    n_init = payload.get("n_init")
    garbage_values = payload.get("garbage_cluster_ids")
    if not model_version:
        raise ValueError(f"{path} is missing model_version")
    if embedding_model != EMBEDDING_MODEL:
        raise ValueError(
            f"{path} must declare embedding_model={EMBEDDING_MODEL!r}"
        )
    if summary_model != SUMMARY_MODEL:
        raise ValueError(
            f"{path} must declare summary_model={SUMMARY_MODEL!r}"
        )
    if not isinstance(k, int) or k < 2:
        raise ValueError(f"{path} has invalid k")
    if not isinstance(random_state, int):
        raise TypeError(f"{path} has invalid random_state")
    if not isinstance(n_init, int) or n_init < 1:
        raise ValueError(f"{path} has invalid n_init")
    if not isinstance(garbage_values, list) or not all(
        isinstance(value, int) for value in garbage_values
    ):
        raise ValueError(f"{path} has invalid garbage_cluster_ids")
    garbage_cluster_ids = frozenset(garbage_values)
    if len(garbage_cluster_ids) != len(garbage_values) or any(
        not 0 <= value < k for value in garbage_cluster_ids
    ):
        raise ValueError(f"{path} has duplicate or out-of-range garbage IDs")
    return ClusterFlags(
        model_version=model_version,
        embedding_model=embedding_model,
        summary_model=summary_model,
        k=k,
        random_state=random_state,
        n_init=n_init,
        garbage_cluster_ids=garbage_cluster_ids,
    )


def load_cluster_store(
    path: Path,
    *,
    flags: ClusterFlags,
    dimensions: int,
) -> ClusterStore:
    """Load reviewed cluster centroids and their Gemini descriptions."""

    if not path.exists():
        raise ValueError(f"Cluster centroid file does not exist: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "centroid",
            "meaning_he",
            "title_he",
            "cluster_size",
            "is_garbage",
            "garbage_reason_he",
            "cluster_model_version",
            "embedding_model",
            "summary_model",
        }
        missing = required.difference(data.files)
        if missing:
            raise ValueError(f"{path} is missing cluster fields: {sorted(missing)}")
        centroids = np.asarray(data["centroid"], dtype=np.float32)
        descriptions = np.asarray(data["meaning_he"], dtype=np.str_)
        titles = np.asarray(data["title_he"], dtype=np.str_)
        cluster_sizes = np.asarray(data["cluster_size"], dtype=np.int64)
        is_garbage = np.asarray(data["is_garbage"], dtype=np.bool_)
        garbage_reasons = np.asarray(data["garbage_reason_he"], dtype=np.str_)
        cluster_model_version = str(
            np.asarray(data["cluster_model_version"]).item()
        )
        embedding_model = str(np.asarray(data["embedding_model"]).item())
        summary_model = str(np.asarray(data["summary_model"]).item())

    if centroids.shape != (flags.k, dimensions):
        raise ValueError(
            f"{path}: centroid shape is {centroids.shape}; expected "
            f"({flags.k}, {dimensions})"
        )
    expected_vector_shape = (flags.k,)
    for label, values in (
        ("title_he", titles),
        ("meaning_he", descriptions),
        ("cluster_size", cluster_sizes),
        ("is_garbage", is_garbage),
        ("garbage_reason_he", garbage_reasons),
    ):
        if values.shape != expected_vector_shape:
            raise ValueError(
                f"{path}: {label} shape is {values.shape}; expected "
                f"{expected_vector_shape}"
            )
    if embedding_model != flags.embedding_model:
        raise ValueError(f"{path}: embedding model does not match configuration")
    if summary_model != flags.summary_model:
        raise ValueError(f"{path}: summary model does not match configuration")
    if cluster_model_version != flags.model_version:
        raise ValueError(
            f"{path}: cluster model version does not match configuration"
        )
    if not np.isfinite(centroids).all() or np.any(cluster_sizes < 1):
        raise ValueError(f"{path}: cluster values are invalid")
    if any(not value.strip() for value in titles.tolist()):
        raise ValueError(f"{path}: cluster title is empty")
    if any(not value.strip() for value in descriptions.tolist()):
        raise ValueError(f"{path}: cluster description is empty")
    if any(not value.strip() for value in garbage_reasons.tolist()):
        raise ValueError(f"{path}: garbage reason is empty")
    generated_garbage_ids = frozenset(
        int(cluster_id) for cluster_id in np.flatnonzero(is_garbage)
    )
    return ClusterStore(
        centroids=centroids,
        titles_he=titles,
        descriptions_he=descriptions,
        cluster_sizes=cluster_sizes,
        garbage_reasons_he=garbage_reasons,
        flags=replace(
            flags,
            garbage_cluster_ids=generated_garbage_ids,
        ),
    )


def save_embedding_store(
    path: Path,
    tweet_ids: np.ndarray,
    embeddings: np.ndarray,
) -> None:
    """Atomically save compact IDs and float32 embeddings."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary_path.open("wb") as file:
            np.savez_compressed(
                file,
                tweet_id=np.asarray(tweet_ids, dtype=np.str_),
                embedding=np.asarray(embeddings, dtype=np.float32),
            )
        _replace_with_retry(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def hydrate_embedding_store_from_bigquery(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    table: str,
    location: str,
    path: Path,
    dimensions: int,
    expected_ids: set[str] | None = None,
) -> int:
    """Copy DB-resident vectors missing from the local checkpoint without API calls."""

    tweet_ids, embeddings = load_embedding_store(path, dimensions=dimensions)
    known_ids = set(tweet_ids.tolist())
    if expected_ids is not None and expected_ids.issubset(known_ids):
        print(
            f"Local embedding checkpoint already covers all "
            f"{len(expected_ids):,} BigQuery posts; skipping hydration",
            flush=True,
        )
        return 0
    table_id = (
        f"{_validate_project(project)}."
        f"{_validate_identifier(dataset, label='dataset')}."
        f"{_validate_identifier(table, label='table')}"
    )
    job = client.query(
        f"""
        SELECT id, embedding
        FROM `{table_id}`
        WHERE ARRAY_LENGTH(embedding) = @dimensions
        """,
        location=location,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("dimensions", "INT64", dimensions)
            ],
            use_query_cache=True,
        ),
    )
    new_ids: list[str] = []
    new_vectors: list[np.ndarray] = []
    rows = job.result()
    for row in tqdm(
        rows,
        total=getattr(rows, "total_rows", None),
        desc="Hydrating DB embeddings",
        unit="vector",
        dynamic_ncols=True,
    ):
        tweet_id = str(row["id"])
        if tweet_id in known_ids:
            continue
        vector = np.asarray(row["embedding"], dtype=np.float32)
        if vector.shape != (dimensions,) or not np.isfinite(vector).all():
            raise ValueError(f"BigQuery tweet {tweet_id!r} has an invalid embedding")
        known_ids.add(tweet_id)
        new_ids.append(tweet_id)
        new_vectors.append(vector)
    if not new_ids:
        return 0
    combined_ids = np.concatenate(
        (tweet_ids, np.asarray(new_ids, dtype=np.str_)),
    )
    combined_embeddings = np.concatenate(
        (embeddings, np.asarray(new_vectors, dtype=np.float32)),
        axis=0,
    )
    order = np.argsort(combined_ids)
    save_embedding_store(path, combined_ids[order], combined_embeddings[order])
    print(
        f"Hydrated {len(new_ids):,} local embeddings from BigQuery",
        flush=True,
    )
    return len(new_ids)


def _replace_with_retry(
    source: Path,
    target: Path,
    *,
    attempts: int = 8,
) -> None:
    """Replace a file despite short-lived Windows scanner/indexer locks."""

    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


def update_embeddings(
    *,
    tweets: list[Tweet],
    path: Path,
    service: AIService,
    run_state: RunState,
    checkpoint_every: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Embed only unseen tweet IDs and checkpoint successful additions."""

    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be at least 1")
    tweet_ids, embeddings = load_embedding_store(
        path,
        dimensions=service.output_dimensions,
    )
    run_state.embeddings_before = len(tweet_ids)
    known_ids = set(tweet_ids.tolist())
    new_tweets = [tweet for tweet in tweets if tweet.tweet_id not in known_ids]
    print(
        f"Embedding store has {len(tweet_ids):,} rows; "
        f"{len(new_tweets):,} new tweets need embeddings",
        flush=True,
    )
    if not new_tweets:
        run_state.embeddings_after = len(tweet_ids)
        return tweet_ids, embeddings

    id_values = tweet_ids.tolist()
    vector_values = [row for row in embeddings]
    try:
        for offset, tweet in enumerate(new_tweets, start=1):
            vector = np.asarray(
                service.embed_text(tweet.text, role="passage"),
                dtype=np.float32,
            )
            if vector.shape != (service.output_dimensions,):
                raise ValueError(
                    f"Tweet {tweet.tweet_id} returned embedding shape {vector.shape}"
                )
            input_tokens = service.last_embedding_input_tokens
            if input_tokens is None:
                raise ValueError("Vertex AI did not return embedding input-token usage")
            id_values.append(tweet.tweet_id)
            vector_values.append(vector)
            run_state.tweets_processed += 1
            run_state.api_usage.embedding_requests += 1
            run_state.api_usage.embedding_input_tokens += input_tokens

            if offset % checkpoint_every == 0:
                tweet_ids = np.asarray(id_values, dtype=np.str_)
                embeddings = np.asarray(vector_values, dtype=np.float32)
                save_embedding_store(path, tweet_ids, embeddings)
                print(
                    f"Embedded {offset:,}/{len(new_tweets):,} new tweets",
                    flush=True,
                )
    except Exception:
        tweet_ids = np.asarray(id_values, dtype=np.str_)
        embeddings = np.asarray(vector_values, dtype=np.float32)
        save_embedding_store(path, tweet_ids, embeddings)
        run_state.embeddings_after = len(tweet_ids)
        raise

    tweet_ids = np.asarray(id_values, dtype=np.str_)
    embeddings = np.asarray(vector_values, dtype=np.float32)
    save_embedding_store(path, tweet_ids, embeddings)
    run_state.embeddings_after = len(tweet_ids)
    print(f"Saved {len(tweet_ids):,} embeddings to {path}", flush=True)
    return tweet_ids, embeddings


def _unit_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Cannot cluster zero-length embeddings")
    return matrix / norms


def _summary_prompt(cluster_id: int, texts: list[str]) -> str:
    examples = "\n\n".join(
        f"{index}. {text}" for index, text in enumerate(texts, start=1)
    )
    return (
        "אתה מנתח שיח פוליטי של חברי כנסת בישראל. "
        "לפניך ציוצים מייצגים מאשכול סמנטי אחד. "
        "זהה את הנושא או המסר המשותף המרכזי בלי לייחס עמדה לכלל הציבור. "
        "התעלם מקישורים, ניסוחים חוזרים ומאפייני פורמט. "
        "החזר כותרת עברית קצרה ותיאור עברי ברור בן שניים עד ארבעה משפטים. "
        "בנוסף, סווג את האשכול באמצעות is_garbage. סמן true רק כאשר רוב "
        "הציוצים אינם מסר פוליטי או ציבורי ממשי, או כאשר אין לאשכול משמעות "
        "קוהרנטית. דוגמאות: טקסט קצר או ריק, ברכות ותודות, אימוג'ים, קישורים "
        "בלבד, תגובות שיחה אישיות, או ערבוב נושאים ללא מסר משותף. עמדה, "
        "ביקורת, הבטחה, דיווח על פעילות ציבורית, מסר בחירות או הנצחה בהקשר "
        "ציבורי אינם garbage. החזר גם garbage_reason_he קצר שמסביר את הסיווג. "
        f"מספר האשכול הוא {cluster_id}.\n\n{examples}"
    )


_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "title_he": {"type": "STRING"},
        "meaning_he": {"type": "STRING"},
        "is_garbage": {"type": "BOOLEAN"},
        "garbage_reason_he": {"type": "STRING"},
    },
    "required": [
        "title_he",
        "meaning_he",
        "is_garbage",
        "garbage_reason_he",
    ],
}


def _parse_summary(generated: GeneratedText) -> tuple[str, str, bool, str]:
    try:
        value = json.loads(generated.text)
    except json.JSONDecodeError as error:
        raise ValueError("Gemini returned invalid summary JSON") from error
    if not isinstance(value, dict):
        raise TypeError("Gemini summary must be a JSON object")
    title = value.get("title_he")
    meaning = value.get("meaning_he")
    is_garbage = value.get("is_garbage")
    garbage_reason = value.get("garbage_reason_he")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Gemini summary is missing title_he")
    if not isinstance(meaning, str) or not meaning.strip():
        raise ValueError("Gemini summary is missing meaning_he")
    if not isinstance(is_garbage, bool):
        raise TypeError("Gemini summary is missing boolean is_garbage")
    if not isinstance(garbage_reason, str) or not garbage_reason.strip():
        garbage_reason = (
            "סווג אוטומטית כאשכול ללא מסר פוליטי או ציבורי ממשי."
            if is_garbage
            else "סווג אוטומטית כאשכול בעל מסר פוליטי או ציבורי ממשי."
        )
    return (
        title.strip(),
        meaning.strip(),
        is_garbage,
        garbage_reason.strip(),
    )


def create_centroids(
    *,
    tweets: list[Tweet],
    tweet_ids: np.ndarray,
    embeddings: np.ndarray,
    path: Path,
    service: AIService,
    run_state: RunState,
    k: int,
    sample_n: int,
    random_state: int,
    cluster_model_version: str | None = None,
) -> None:
    """Cluster, summarize, classify garbage clusters, and save one artifact."""

    if path.exists():
        with np.load(path, allow_pickle=False) as existing:
            existing_k = (
                len(existing["centroid"])
                if "centroid" in existing.files
                else 0
            )
            has_automatic_garbage = {
                "is_garbage",
                "garbage_reason_he",
            }.issubset(existing.files)
            existing_model_version = (
                str(np.asarray(existing["cluster_model_version"]).item())
                if "cluster_model_version" in existing.files
                else None
            )
        model_version_matches = (
            cluster_model_version is None
            or existing_model_version == cluster_model_version
        )
        if (
            existing_k == k
            and has_automatic_garbage
            and model_version_matches
        ):
            print(f"Centroid file already exists at {path}; skipping", flush=True)
            return
        print(
            f"Centroid file at {path} has K={existing_k} or lacks automatic "
            f"garbage decisions; recomputing K={k}",
            flush=True,
        )
    if not 1 < k <= len(tweet_ids):
        raise ValueError(f"k must be between 2 and {len(tweet_ids)}")
    if sample_n < 1:
        raise ValueError("sample_n must be at least 1")

    normalized = _unit_rows(embeddings)
    print(
        f"Fitting K-means over {len(tweet_ids):,} embeddings: K={k}",
        flush=True,
    )
    model = KMeans(
        n_clusters=k,
        init="k-means++",
        n_init=DEFAULT_N_INIT,
        max_iter=500,
        random_state=random_state,
        algorithm="lloyd",
    )
    original_labels = model.fit_predict(normalized)
    original_counts = np.bincount(original_labels, minlength=k)
    cluster_order = sorted(
        range(k),
        key=lambda cluster_id: (-int(original_counts[cluster_id]), cluster_id),
    )
    remap = {
        original_cluster_id: cluster_id
        for cluster_id, original_cluster_id in enumerate(cluster_order)
    }
    labels = np.asarray(
        [remap[int(label)] for label in original_labels],
        dtype=np.int64,
    )
    centroids = np.asarray(
        model.cluster_centers_[cluster_order],
        dtype=np.float32,
    )
    unit_centroids = _unit_rows(centroids)
    similarities = np.einsum(
        "ij,ij->i",
        normalized,
        unit_centroids[labels],
    )
    text_by_id = {tweet.tweet_id: tweet.text for tweet in tweets}

    titles: list[str] = []
    meanings: list[str] = []
    garbage_flags: list[bool] = []
    garbage_reasons: list[str] = []
    cluster_sizes: list[int] = []
    sampled_ids: list[list[str]] = []
    for cluster_id in range(k):
        member_indices = np.flatnonzero(labels == cluster_id)
        available_indices = np.asarray(
            [index for index in member_indices if str(tweet_ids[index]) in text_by_id],
            dtype=np.int64,
        )
        if len(available_indices) == 0:
            raise ValueError(f"Cluster {cluster_id} has no source text available")
        ranked = available_indices[np.argsort(similarities[available_indices])[::-1]]
        representative_indices = ranked[:sample_n]
        representative_ids = [str(tweet_ids[index]) for index in representative_indices]
        representative_texts = [text_by_id[tweet_id] for tweet_id in representative_ids]
        print(
            f"Summarizing cluster {cluster_id + 1}/{k} from "
            f"{len(representative_texts)} representative tweets",
            flush=True,
        )
        generated = service.generate_text(
            _summary_prompt(cluster_id, representative_texts),
            model=SUMMARY_MODEL,
            max_output_tokens=512,
            temperature=0.2,
            thinking_budget=0,
            response_mime_type="application/json",
            response_schema=_SUMMARY_SCHEMA,
        )
        run_state.api_usage.summarization_requests += 1
        run_state.api_usage.summarization_input_tokens += generated.input_tokens
        run_state.api_usage.summarization_output_tokens += generated.output_tokens
        title, meaning, is_garbage, garbage_reason = _parse_summary(generated)
        titles.append(title)
        meanings.append(meaning)
        garbage_flags.append(is_garbage)
        garbage_reasons.append(garbage_reason)
        cluster_sizes.append(len(member_indices))
        sampled_ids.append(representative_ids)
        print(
            f"Cluster {cluster_id} summary received; "
            f"is_garbage={is_garbage}",
            flush=True,
        )

    max_samples = max(len(values) for values in sampled_ids)
    max_id_length = max(
        len(tweet_id) for cluster_ids in sampled_ids for tweet_id in cluster_ids
    )
    sample_array = np.full(
        (k, max_samples),
        "",
        dtype=f"<U{max_id_length}",
    )
    for cluster_id, values in enumerate(sampled_ids):
        sample_array[cluster_id, : len(values)] = values

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary_path.open("wb") as file:
            np.savez_compressed(
                file,
                centroid=centroids,
                meaning_he=np.asarray(meanings, dtype=np.str_),
                title_he=np.asarray(titles, dtype=np.str_),
                cluster_size=np.asarray(cluster_sizes, dtype=np.int64),
                is_garbage=np.asarray(garbage_flags, dtype=np.bool_),
                garbage_reason_he=np.asarray(
                    garbage_reasons,
                    dtype=np.str_,
                ),
                cluster_model_version=np.asarray(
                    cluster_model_version or "",
                ),
                sample_tweet_id=sample_array,
                embedding_model=np.asarray(EMBEDDING_MODEL),
                summary_model=np.asarray(SUMMARY_MODEL),
                k=np.asarray(k, dtype=np.int64),
                random_state=np.asarray(random_state, dtype=np.int64),
                n_init=np.asarray(DEFAULT_N_INIT, dtype=np.int64),
            )
        _replace_with_retry(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    run_state.centroids_updated = True
    print(f"Saved centroids and Hebrew meanings to {path}", flush=True)


def upload_tweet_clusters(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    location: str,
    store: ClusterStore,
) -> int:
    """Upsert reviewed K-means centroids and metadata into BigQuery."""

    project = _validate_project(project)
    dataset = _validate_identifier(dataset, label="dataset")
    staging_id = f"{project}.{dataset}.stg_tweet_clusters"
    target_id = f"{project}.{dataset}.tweet_cluster"
    rows = [
        {
            "model_version": store.flags.model_version,
            "cluster_id": cluster_id,
            "centroid": store.centroids[cluster_id].astype(float).tolist(),
            "title_he": str(store.titles_he[cluster_id]),
            "description_he": str(store.descriptions_he[cluster_id]),
            "cluster_size": int(store.cluster_sizes[cluster_id]),
            "is_garbage": cluster_id in store.flags.garbage_cluster_ids,
            "embedding_model": store.flags.embedding_model,
            "summary_model": store.flags.summary_model,
            "k": store.flags.k,
            "random_state": store.flags.random_state,
            "n_init": store.flags.n_init,
        }
        for cluster_id in range(store.flags.k)
    ]
    load_job = client.load_table_from_json(
        rows,
        staging_id,
        location=location,
        job_config=bigquery.LoadJobConfig(
            schema=[
                bigquery.SchemaField(
                    "model_version",
                    "STRING",
                    mode="REQUIRED",
                ),
                bigquery.SchemaField("cluster_id", "INT64", mode="REQUIRED"),
                bigquery.SchemaField(
                    "centroid",
                    "FLOAT64",
                    mode="REPEATED",
                ),
                bigquery.SchemaField("title_he", "STRING", mode="REQUIRED"),
                bigquery.SchemaField(
                    "description_he",
                    "STRING",
                    mode="REQUIRED",
                ),
                bigquery.SchemaField(
                    "cluster_size",
                    "INT64",
                    mode="REQUIRED",
                ),
                bigquery.SchemaField(
                    "is_garbage",
                    "BOOL",
                    mode="REQUIRED",
                ),
                bigquery.SchemaField(
                    "embedding_model",
                    "STRING",
                    mode="REQUIRED",
                ),
                bigquery.SchemaField(
                    "summary_model",
                    "STRING",
                    mode="REQUIRED",
                ),
                bigquery.SchemaField("k", "INT64", mode="REQUIRED"),
                bigquery.SchemaField(
                    "random_state",
                    "INT64",
                    mode="REQUIRED",
                ),
                bigquery.SchemaField("n_init", "INT64", mode="REQUIRED"),
            ],
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    load_job.result()
    merge_job = client.query(
        f"""
        MERGE `{target_id}` t
        USING `{staging_id}` s
        ON t.model_version = s.model_version
           AND t.cluster_id = s.cluster_id
        WHEN MATCHED THEN UPDATE SET
          centroid = s.centroid,
          title_he = s.title_he,
          description_he = s.description_he,
          cluster_size = s.cluster_size,
          is_garbage = s.is_garbage,
          embedding_model = s.embedding_model,
          summary_model = s.summary_model,
          k = s.k,
          random_state = s.random_state,
          n_init = s.n_init,
          updated_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
          model_version, cluster_id, centroid, title_he, description_he,
          cluster_size, is_garbage, embedding_model, summary_model, k,
          random_state, n_init
        )
        VALUES (
          s.model_version, s.cluster_id, s.centroid, s.title_he,
          s.description_he, s.cluster_size, s.is_garbage,
          s.embedding_model, s.summary_model, s.k, s.random_state, s.n_init
        )
        """,
        location=location,
    )
    merge_job.result()
    return int(merge_job.num_dml_affected_rows or 0)


def _usd(tokens: int, rate: Decimal) -> Decimal:
    return Decimal(tokens) * rate / ONE_MILLION


def _cost_breakdown(usage: ApiUsage) -> dict[str, float]:
    embedding = _usd(
        usage.embedding_input_tokens,
        EMBEDDING_INPUT_USD_PER_MILLION_TOKENS,
    )
    summary_input = _usd(
        usage.summarization_input_tokens,
        FLASH_INPUT_USD_PER_MILLION_TOKENS,
    )
    summary_output = _usd(
        usage.summarization_output_tokens,
        FLASH_OUTPUT_USD_PER_MILLION_TOKENS,
    )
    total = embedding + summary_input + summary_output
    return {
        "embedding_input_usd": float(embedding),
        "summarization_input_usd": float(summary_input),
        "summarization_output_usd": float(summary_output),
        "total_cost_usd": float(total),
    }


def _history_record(
    *,
    run_state: RunState,
    project: str,
    dataset: str,
    table: str,
    embedding_file: Path,
    centroid_file: Path,
    cluster_flags_file: Path,
    anchor_definitions_file: Path,
    anchor_embedding_file: Path,
    softmax_temperature: float,
    cluster_model_version: str | None,
) -> dict[str, Any]:
    record = asdict(run_state)
    issue_model = (
        issue_score_model_version(
            softmax_temperature,
            cluster_model_version=cluster_model_version,
        )
        if cluster_model_version is not None
        else issue_score_model_version(softmax_temperature)
    )
    record.update(
        {
            "source_table": f"{project}.{dataset}.{table}",
            "embedding_file": str(embedding_file),
            "centroid_file": str(centroid_file),
            "cluster_flags_file": str(cluster_flags_file),
            "anchor_definitions_file": str(anchor_definitions_file),
            "anchor_embedding_file": str(anchor_embedding_file),
            "models": {
                "embedding": EMBEDDING_MODEL,
                "summarization": SUMMARY_MODEL,
                "issue_scoring": issue_model,
            },
            "issue_scoring": {
                "softmax_temperature": softmax_temperature,
                "anchors_per_issue": 8,
                "minimum_text_length_after_url_removal": 16,
            },
            "api_cost_usd": _cost_breakdown(run_state.api_usage),
            "pricing": {
                "source": PRICING_SOURCE,
                "embedding_input_per_million_tokens": float(
                    EMBEDDING_INPUT_USD_PER_MILLION_TOKENS
                ),
                "flash_input_per_million_tokens": float(
                    FLASH_INPUT_USD_PER_MILLION_TOKENS
                ),
                "flash_output_per_million_tokens": float(
                    FLASH_OUTPUT_USD_PER_MILLION_TOKENS
                ),
                "bigquery_cost_included": False,
            },
        }
    )
    return record


def append_run_history(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        file.write("\n")


def run_pipeline(
    *,
    project: str,
    dataset: str,
    table: str,
    bigquery_location: str,
    embedding_file: Path,
    centroid_file: Path,
    history_file: Path,
    checkpoint_every: int,
    k: int,
    sample_n: int,
    random_state: int,
    cluster_flags_file: Path = DEFAULT_CLUSTER_FLAGS_FILE,
    anchor_definitions_file: Path = DEFAULT_ANCHOR_DEFINITIONS_FILE,
    anchor_embedding_file: Path = DEFAULT_ANCHOR_EMBEDDING_FILE,
    initialize_anchors: bool = False,
    anchor_workers: int = 8,
    softmax_temperature: float = DEFAULT_SOFTMAX_TEMPERATURE,
    sync_database: bool = True,
    bigquery_client: bigquery.Client | None = None,
    ai_service: AIService | None = None,
) -> RunState:
    """Run the full pipeline and append history even when the run fails."""

    state = RunState(started_at=_utc_now())
    cluster_model_version: str | None = None
    cluster_flags: ClusterFlags | None = None
    try:
        if sync_database:
            cluster_flags = load_cluster_flags(cluster_flags_file)
            cluster_model_version = cluster_flags.model_version
            if (
                k != cluster_flags.k
                or random_state != cluster_flags.random_state
                or DEFAULT_N_INIT != cluster_flags.n_init
            ):
                raise ValueError(
                    "Runtime clustering parameters do not match the "
                    f"configuration in {cluster_flags_file}"
                )
        tweets, state.bigquery = fetch_tweets(
            project=project,
            dataset=dataset,
            table=table,
            location=bigquery_location,
            client=bigquery_client,
        )
        state.source_tweets = len(tweets)
        service = ai_service or VertexAIService(
            project=project,
            location=DEFAULT_VERTEX_LOCATION,
            output_dimensions=DEFAULT_EMBEDDING_DIMENSIONS,
            progress=lambda message: print(message, flush=True),
        )
        query_client = bigquery_client or bigquery.Client(
            project=project,
            location=bigquery_location,
        )
        if sync_database:
            hydrate_embedding_store_from_bigquery(
                client=query_client,
                project=project,
                dataset=dataset,
                table=table,
                location=bigquery_location,
                path=embedding_file,
                dimensions=service.output_dimensions,
                expected_ids={tweet.tweet_id for tweet in tweets},
            )
        tweet_ids, embeddings = update_embeddings(
            tweets=tweets,
            path=embedding_file,
            service=service,
            run_state=state,
            checkpoint_every=checkpoint_every,
        )
        create_centroids(
            tweets=tweets,
            tweet_ids=tweet_ids,
            embeddings=embeddings,
            path=centroid_file,
            service=service,
            run_state=state,
            k=k,
            sample_n=sample_n,
            random_state=random_state,
            cluster_model_version=cluster_model_version,
        )
        if sync_database:
            assert cluster_flags is not None
            cluster_store = load_cluster_store(
                centroid_file,
                flags=cluster_flags,
                dimensions=service.output_dimensions,
            )
            state.garbage_clusters = len(
                cluster_store.flags.garbage_cluster_ids
            )
            state.clusters_uploaded = upload_tweet_clusters(
                client=query_client,
                project=project,
                dataset=dataset,
                location=bigquery_location,
                store=cluster_store,
            )
            issues = fetch_issues(
                client=query_client,
                project=project,
                dataset=dataset,
                location=bigquery_location,
            )
            if initialize_anchors:
                definitions = load_anchor_definitions(anchor_definitions_file)
                anchor_store, anchor_usage = initialize_anchor_embeddings(
                    definitions=definitions,
                    path=anchor_embedding_file,
                    service=service,
                    checkpoint_every=8,
                    max_workers=anchor_workers,
                )
                state.anchors_embedded = anchor_usage.requests
                state.api_usage.embedding_requests += anchor_usage.requests
                state.api_usage.embedding_input_tokens += anchor_usage.input_tokens
                state.anchors_uploaded = upload_issue_anchors(
                    client=query_client,
                    project=project,
                    dataset=dataset,
                    location=bigquery_location,
                    issues=issues,
                    store=anchor_store,
                )

            anchors = fetch_issue_anchors(
                client=query_client,
                project=project,
                dataset=dataset,
                location=bigquery_location,
                dimensions=service.output_dimensions,
            )
            missing_db_embedding_ids = [
                tweet.tweet_id for tweet in tweets if tweet.needs_db_embedding
            ]
            state.db_embeddings_backfilled = sync_missing_tweet_embeddings(
                client=query_client,
                project=project,
                dataset=dataset,
                table=table,
                location=bigquery_location,
                missing_post_ids=missing_db_embedding_ids,
                tweet_ids=tweet_ids,
                embeddings=embeddings,
                dimensions=service.output_dimensions,
            )
            existing_pairs = fetch_existing_post_issue_pairs(
                client=query_client,
                project=project,
                dataset=dataset,
                location=bigquery_location,
                model_version=issue_score_model_version(
                    softmax_temperature,
                    cluster_model_version=cluster_flags.model_version,
                ),
            )
            score_rows = compute_missing_issue_scores(
                live_post_ids=[tweet.tweet_id for tweet in tweets],
                post_text_by_id={
                    tweet.tweet_id: tweet.text for tweet in tweets
                },
                tweet_ids=tweet_ids,
                tweet_embeddings=embeddings,
                issues=issues,
                anchors=anchors,
                cluster_centroids=cluster_store.centroids,
                garbage_cluster_ids=cluster_store.flags.garbage_cluster_ids,
                existing_pairs=existing_pairs,
                temperature=softmax_temperature,
                cluster_model_version=cluster_flags.model_version,
                show_progress=True,
            )
            state.issue_score_rows_computed = len(score_rows)
            state.issue_score_rows_inserted = upload_issue_scores(
                client=query_client,
                project=project,
                dataset=dataset,
                location=bigquery_location,
                rows=score_rows,
            )
        state.status = "completed"
        return state
    except Exception as error:
        state.status = "failed"
        state.error = f"{type(error).__name__}: {error}"
        raise
    finally:
        state.finished_at = _utc_now()
        append_run_history(
            history_file,
            _history_record(
                run_state=state,
                project=project,
                dataset=dataset,
                table=table,
                embedding_file=embedding_file,
                centroid_file=centroid_file,
                cluster_flags_file=cluster_flags_file,
                anchor_definitions_file=anchor_definitions_file,
                anchor_embedding_file=anchor_embedding_file,
                softmax_temperature=softmax_temperature,
                cluster_model_version=cluster_model_version,
            ),
        )
        print(
            f"Run history appended to {history_file}; "
            "Gemini API cost="
            f"${_cost_breakdown(state.api_usage)['total_cost_usd']:.8f}",
            flush=True,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read BigQuery tweets, incrementally embed them, and create "
            "K-means centroids with Hebrew Gemini summaries."
        )
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument(
        "--bigquery-location",
        default=DEFAULT_BIGQUERY_LOCATION,
    )
    parser.add_argument(
        "--embedding-file",
        type=Path,
        default=DEFAULT_EMBEDDING_FILE,
    )
    parser.add_argument(
        "--centroid-file",
        type=Path,
        default=DEFAULT_CENTROID_FILE,
    )
    parser.add_argument(
        "--cluster-flags-file",
        type=Path,
        default=DEFAULT_CLUSTER_FLAGS_FILE,
    )
    parser.add_argument(
        "--history-file",
        type=Path,
        default=DEFAULT_RUN_HISTORY_FILE,
    )
    parser.add_argument(
        "--anchor-definitions-file",
        type=Path,
        default=DEFAULT_ANCHOR_DEFINITIONS_FILE,
    )
    parser.add_argument(
        "--anchor-embedding-file",
        type=Path,
        default=DEFAULT_ANCHOR_EMBEDDING_FILE,
    )
    parser.add_argument(
        "--initialize-anchors",
        action="store_true",
        help=(
            "One-time operation: embed the hand-curated anchor definitions "
            "and insert missing issue_anchor rows before scoring"
        ),
    )
    parser.add_argument(
        "--anchor-workers",
        type=int,
        default=8,
        help="Concurrent Vertex embedding requests for missing issue anchors",
    )
    parser.add_argument(
        "--softmax-temperature",
        type=float,
        default=DEFAULT_SOFTMAX_TEMPERATURE,
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
    )
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--sample-n", type=int, default=DEFAULT_SAMPLE_N)
    parser.add_argument(
        "--random-state",
        type=int,
        default=DEFAULT_RANDOM_STATE,
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    run_pipeline(
        project=args.project,
        dataset=args.dataset,
        table=args.table,
        bigquery_location=args.bigquery_location,
        embedding_file=args.embedding_file,
        centroid_file=args.centroid_file,
        cluster_flags_file=args.cluster_flags_file,
        history_file=args.history_file,
        anchor_definitions_file=args.anchor_definitions_file,
        anchor_embedding_file=args.anchor_embedding_file,
        initialize_anchors=args.initialize_anchors,
        anchor_workers=args.anchor_workers,
        softmax_temperature=args.softmax_temperature,
        checkpoint_every=args.checkpoint_every,
        k=args.k,
        sample_n=args.sample_n,
        random_state=args.random_state,
    )


if __name__ == "__main__":
    main()
