"""BigQuery embedding backfill and multilingual issue-similarity scoring."""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import numpy as np
from google.cloud import bigquery
from tqdm.auto import tqdm

from mk_tracking.pipeline import EMBEDDING_MODEL

ANCHORS_PER_ISSUE = 8
EXPECTED_ISSUE_COUNT = 7
ANCHOR_LANGUAGE_COUNTS = {"he": 4, "en": 2, "ar": 2}
DEFAULT_SOFTMAX_TEMPERATURE = 0.07
MIN_SCORABLE_TEXT_LENGTH = 16
DEFAULT_CLUSTER_MODEL_VERSION = "gemini-embedding-2:kmeans-k30-rs42-n1:v1"
_URL_PATTERN = re.compile(r"https?://\S+", flags=re.IGNORECASE)


def issue_score_model_version(
    temperature: float,
    *,
    cluster_model_version: str = DEFAULT_CLUSTER_MODEL_VERSION,
) -> str:
    """Return provenance that includes the score calibration."""

    return (
        f"{EMBEDDING_MODEL}:max8:softmax-t{temperature:g}:"
        f"short{MIN_SCORABLE_TEXT_LENGTH}:{cluster_model_version}:v2"
    )


ISSUE_SCORE_MODEL_VERSION = issue_score_model_version(DEFAULT_SOFTMAX_TEMPERATURE)

_PROJECT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AnchorDefinition:
    issue_slug: str
    anchor_index: int
    language: str
    text: str


@dataclass(frozen=True)
class Issue:
    issue_id: str
    slug: str
    name: str
    description: str
    social_post_prompt: str
    sort_order: int


@dataclass(frozen=True)
class IssueAnchor:
    issue_id: str
    issue_slug: str
    anchor_index: int
    language: str
    text: str
    embedding: np.ndarray
    embedding_model: str


@dataclass(frozen=True)
class AnchorEmbeddingStore:
    definitions: tuple[AnchorDefinition, ...]
    embeddings: np.ndarray
    embedding_model: str


@dataclass(frozen=True)
class AnchorInitializationUsage:
    requests: int
    input_tokens: int


class EmbeddingService(Protocol):
    embedding_model: str
    output_dimensions: int
    last_embedding_input_tokens: int | None

    def embed_text(self, text: str, *, role: str) -> list[float]:
        """Return one normalized embedding."""


def _validated_table_id(
    project: str,
    dataset: str,
    table: str,
) -> str:
    if not _PROJECT_PATTERN.fullmatch(project):
        raise ValueError(f"Invalid GCP project ID: {project!r}")
    for label, value in (("dataset", dataset), ("table", table)):
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"Invalid BigQuery {label}: {value!r}")
    return f"{project}.{dataset}.{table}"


def load_anchor_definitions(path: Path) -> tuple[AnchorDefinition, ...]:
    """Load and strictly validate the hand-curated multilingual anchors."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Could not read issue anchor definitions from {path}"
        ) from error

    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    if payload.get("embedding_model") != EMBEDDING_MODEL:
        raise ValueError(f"{path} must declare embedding_model={EMBEDDING_MODEL!r}")
    issue_rows = payload.get("issues")
    if not isinstance(issue_rows, list) or len(issue_rows) != EXPECTED_ISSUE_COUNT:
        raise ValueError(f"{path} must contain exactly {EXPECTED_ISSUE_COUNT} issues")

    definitions: list[AnchorDefinition] = []
    seen_slugs: set[str] = set()
    for issue_position, issue_row in enumerate(issue_rows, start=1):
        if not isinstance(issue_row, dict):
            raise TypeError(f"Issue {issue_position} in {path} must be an object")
        slug = str(issue_row.get("slug") or "").strip()
        if not slug or slug in seen_slugs:
            raise ValueError(f"Invalid or duplicate issue slug {slug!r} in {path}")
        seen_slugs.add(slug)
        anchors = issue_row.get("anchors")
        if not isinstance(anchors, list) or len(anchors) != ANCHORS_PER_ISSUE:
            raise ValueError(
                f"Issue {slug!r} must contain exactly {ANCHORS_PER_ISSUE} anchors"
            )

        languages: Counter[str] = Counter()
        indices: set[int] = set()
        for anchor_position, anchor_row in enumerate(anchors, start=1):
            if not isinstance(anchor_row, dict):
                raise TypeError(
                    f"Anchor {anchor_position} for {slug!r} must be an object"
                )
            anchor_index = anchor_row.get("anchor_index")
            if not isinstance(anchor_index, int):
                raise TypeError(f"anchor_index for {slug!r} must be an integer")
            language = str(anchor_row.get("language") or "").strip()
            text = str(anchor_row.get("text") or "").strip()
            if anchor_index in indices or not 1 <= anchor_index <= ANCHORS_PER_ISSUE:
                raise ValueError(
                    f"Invalid or duplicate anchor_index {anchor_index} for {slug!r}"
                )
            if language not in ANCHOR_LANGUAGE_COUNTS:
                raise ValueError(
                    f"Unsupported anchor language {language!r} for {slug!r}"
                )
            if not text:
                raise ValueError(f"Empty anchor text for {slug!r}")
            indices.add(anchor_index)
            languages[language] += 1
            definitions.append(
                AnchorDefinition(
                    issue_slug=slug,
                    anchor_index=anchor_index,
                    language=language,
                    text=text,
                )
            )
        if dict(languages) != ANCHOR_LANGUAGE_COUNTS:
            raise ValueError(
                f"Issue {slug!r} has language counts {dict(languages)}; "
                f"expected {ANCHOR_LANGUAGE_COUNTS}"
            )

    return tuple(definitions)


def _empty_anchor_store(
    *,
    dimensions: int,
    embedding_model: str,
) -> AnchorEmbeddingStore:
    return AnchorEmbeddingStore(
        definitions=(),
        embeddings=np.empty((0, dimensions), dtype=np.float32),
        embedding_model=embedding_model,
    )


def load_anchor_embedding_store(
    path: Path,
    *,
    dimensions: int,
    embedding_model: str = EMBEDDING_MODEL,
) -> AnchorEmbeddingStore:
    """Load a resumable local NPZ cache of curated anchor embeddings."""

    if not path.exists():
        return _empty_anchor_store(
            dimensions=dimensions,
            embedding_model=embedding_model,
        )
    with np.load(path, allow_pickle=False) as data:
        expected_files = {
            "issue_slug",
            "anchor_index",
            "language",
            "text",
            "embedding",
            "embedding_model",
        }
        if set(data.files) != expected_files:
            raise ValueError(f"{path} must contain exactly {sorted(expected_files)}")
        slugs = np.asarray(data["issue_slug"], dtype=np.str_)
        indices = np.asarray(data["anchor_index"], dtype=np.int64)
        languages = np.asarray(data["language"], dtype=np.str_)
        texts = np.asarray(data["text"], dtype=np.str_)
        embeddings = np.asarray(data["embedding"], dtype=np.float32)
        stored_model = str(np.asarray(data["embedding_model"]).item())

    row_count = len(slugs)
    if not (
        indices.shape == (row_count,)
        and languages.shape == (row_count,)
        and texts.shape == (row_count,)
        and embeddings.shape == (row_count, dimensions)
    ):
        raise ValueError(f"{path} contains inconsistent anchor array shapes")
    if stored_model != embedding_model:
        raise ValueError(f"{path} uses {stored_model!r}; expected {embedding_model!r}")
    if not np.isfinite(embeddings).all():
        raise ValueError(f"{path} contains non-finite anchor embeddings")

    definitions = tuple(
        AnchorDefinition(
            issue_slug=str(slug),
            anchor_index=int(index),
            language=str(language),
            text=str(text),
        )
        for slug, index, language, text in zip(
            slugs,
            indices,
            languages,
            texts,
            strict=True,
        )
    )
    keys = [(row.issue_slug, row.anchor_index) for row in definitions]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{path} contains duplicate issue anchor keys")
    return AnchorEmbeddingStore(
        definitions=definitions,
        embeddings=embeddings,
        embedding_model=stored_model,
    )


def save_anchor_embedding_store(
    path: Path,
    store: AnchorEmbeddingStore,
) -> None:
    """Atomically save the local curated-anchor embedding cache."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        with temporary_path.open("wb") as file:
            np.savez_compressed(
                file,
                issue_slug=np.asarray(
                    [row.issue_slug for row in store.definitions],
                    dtype=np.str_,
                ),
                anchor_index=np.asarray(
                    [row.anchor_index for row in store.definitions],
                    dtype=np.int64,
                ),
                language=np.asarray(
                    [row.language for row in store.definitions],
                    dtype=np.str_,
                ),
                text=np.asarray(
                    [row.text for row in store.definitions],
                    dtype=np.str_,
                ),
                embedding=np.asarray(store.embeddings, dtype=np.float32),
                embedding_model=np.asarray(store.embedding_model),
            )
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def initialize_anchor_embeddings(
    *,
    definitions: Sequence[AnchorDefinition],
    path: Path,
    service: EmbeddingService,
    checkpoint_every: int = 8,
    max_workers: int = 1,
) -> tuple[AnchorEmbeddingStore, AnchorInitializationUsage]:
    """Embed missing hand-curated anchors once and resume from checkpoints."""

    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be at least 1")
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    existing = load_anchor_embedding_store(
        path,
        dimensions=service.output_dimensions,
        embedding_model=service.embedding_model,
    )
    expected_by_key = {(row.issue_slug, row.anchor_index): row for row in definitions}
    existing_by_key = {
        (row.issue_slug, row.anchor_index): (row, existing.embeddings[index])
        for index, row in enumerate(existing.definitions)
    }
    if not set(existing_by_key).issubset(expected_by_key):
        raise ValueError(f"{path} contains anchors absent from the definitions")
    for key, (stored, _) in existing_by_key.items():
        if stored != expected_by_key[key]:
            raise ValueError(f"{path} conflicts with the curated definition for {key}")

    embeddings_by_key = {
        key: np.asarray(row[1], dtype=np.float32)
        for key, row in existing_by_key.items()
    }
    requests = 0
    input_tokens = 0

    missing = [
        definition
        for definition in definitions
        if (definition.issue_slug, definition.anchor_index) not in existing_by_key
    ]
    legacy_service_lock = Lock()

    def embed_one(
        definition: AnchorDefinition,
    ) -> tuple[AnchorDefinition, np.ndarray, int]:
        embed_with_usage = getattr(service, "embed_text_with_usage", None)
        if callable(embed_with_usage):
            values, tokens = embed_with_usage(definition.text, role="query")
        else:
            # Older/test services expose usage through shared mutable state.
            # Serialize that fallback so the vector and token count stay paired.
            with legacy_service_lock:
                values = service.embed_text(definition.text, role="query")
                tokens = service.last_embedding_input_tokens
        if tokens is None:
            raise ValueError("Vertex AI did not return anchor embedding token usage")
        vector = np.asarray(values, dtype=np.float32)
        key = (definition.issue_slug, definition.anchor_index)
        if vector.shape != (service.output_dimensions,):
            raise ValueError(f"Anchor {key} returned embedding shape {vector.shape}")
        return definition, vector, int(tokens)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(missing) or 1)) as pool:
        futures = [pool.submit(embed_one, definition) for definition in missing]
        for future in as_completed(futures):
            definition, vector, tokens = future.result()
            key = (definition.issue_slug, definition.anchor_index)
            embeddings_by_key[key] = vector
            requests += 1
            input_tokens += tokens
            if requests % checkpoint_every == 0:
                completed_definitions = tuple(
                    row
                    for row in definitions
                    if (row.issue_slug, row.anchor_index) in embeddings_by_key
                )
                completed_embeddings = np.asarray(
                    [
                        embeddings_by_key[(row.issue_slug, row.anchor_index)]
                        for row in completed_definitions
                    ],
                    dtype=np.float32,
                )
                save_anchor_embedding_store(
                    path,
                    AnchorEmbeddingStore(
                        definitions=completed_definitions,
                        embeddings=completed_embeddings,
                        embedding_model=service.embedding_model,
                    ),
                )

    ordered_definitions = list(definitions)
    ordered_embeddings = [
        embeddings_by_key[(row.issue_slug, row.anchor_index)]
        for row in ordered_definitions
    ]

    store = AnchorEmbeddingStore(
        definitions=tuple(ordered_definitions),
        embeddings=np.asarray(ordered_embeddings, dtype=np.float32),
        embedding_model=service.embedding_model,
    )
    save_anchor_embedding_store(path, store)
    return store, AnchorInitializationUsage(
        requests=requests,
        input_tokens=input_tokens,
    )


def fetch_issues(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    location: str,
) -> list[Issue]:
    """Read the authoritative issue rows from BigQuery."""

    table_id = _validated_table_id(project, dataset, "issue")
    query = f"""
        SELECT id, slug, name, description,
               prompt_for_social_post_similarity, sort_order
        FROM `{table_id}`
        ORDER BY sort_order, slug
    """
    rows = client.query(query, location=location).result()
    issues = [
        Issue(
            issue_id=str(row["id"]),
            slug=str(row["slug"]),
            name=str(row["name"]),
            description=str(row["description"]),
            social_post_prompt=str(row["prompt_for_social_post_similarity"]),
            sort_order=int(row["sort_order"]),
        )
        for row in rows
    ]
    if len(issues) != EXPECTED_ISSUE_COUNT:
        raise ValueError(
            f"BigQuery contains {len(issues)} issues; expected {EXPECTED_ISSUE_COUNT}"
        )
    return issues


def _batch_load_jsonl(
    *,
    client: bigquery.Client,
    table_id: str,
    rows: Iterable[Mapping[str, Any]],
    schema: Sequence[bigquery.SchemaField],
    location: str,
) -> int:
    """Batch-load rows into a replaceable staging table."""

    temporary_path: Path | None = None
    row_count = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            suffix=".jsonl",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            for row in rows:
                file.write(
                    json.dumps(
                        dict(row),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                )
                file.write("\n")
                row_count += 1
        if row_count == 0:
            return 0
        with temporary_path.open("rb") as file:
            job = client.load_table_from_file(
                file,
                table_id,
                location=location,
                job_config=bigquery.LoadJobConfig(
                    schema=list(schema),
                    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                    write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                ),
            )
            job.result()
        return row_count
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def upload_issue_anchors(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    location: str,
    issues: Sequence[Issue],
    store: AnchorEmbeddingStore,
) -> int:
    """Insert missing curated anchor rows without overwriting manual edits."""

    issue_by_slug = {issue.slug: issue for issue in issues}
    definition_slugs = {row.issue_slug for row in store.definitions}
    if definition_slugs != set(issue_by_slug):
        raise ValueError("Anchor-definition slugs do not match the live issue taxonomy")
    if store.embeddings.shape[0] != len(store.definitions):
        raise ValueError("Anchor embedding store has inconsistent row counts")

    staging_id = _validated_table_id(
        project,
        dataset,
        "stg_issue_anchors",
    )
    target_id = _validated_table_id(project, dataset, "issue_anchor")
    issue_id = _validated_table_id(project, dataset, "issue")
    rows = (
        {
            "issue_slug": definition.issue_slug,
            "anchor_index": definition.anchor_index,
            "language": definition.language,
            "text": definition.text,
            "embedding": store.embeddings[index].astype(float).tolist(),
            "embedding_model": store.embedding_model,
        }
        for index, definition in enumerate(store.definitions)
    )
    staged = _batch_load_jsonl(
        client=client,
        table_id=staging_id,
        rows=rows,
        schema=[
            bigquery.SchemaField("issue_slug", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("anchor_index", "INT64", mode="REQUIRED"),
            bigquery.SchemaField("language", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("text", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
            bigquery.SchemaField("embedding_model", "STRING", mode="REQUIRED"),
        ],
        location=location,
    )
    if staged == 0:
        return 0

    merge = f"""
        MERGE `{target_id}` t
        USING (
          SELECT i.id AS issue_id, s.anchor_index, s.language, s.text,
                 s.embedding, s.embedding_model
          FROM `{staging_id}` s
          JOIN `{issue_id}` i ON i.slug = s.issue_slug
        ) s
        ON t.issue_id = s.issue_id AND t.anchor_index = s.anchor_index
        WHEN NOT MATCHED THEN INSERT (
          id, issue_id, anchor_index, language, text, embedding,
          embedding_model
        )
        VALUES (
          GENERATE_UUID(), s.issue_id, s.anchor_index, s.language, s.text,
          s.embedding, s.embedding_model
        )
    """
    job = client.query(merge, location=location)
    job.result()
    return int(job.num_dml_affected_rows or 0)


def fetch_issue_anchors(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    location: str,
    dimensions: int,
) -> list[IssueAnchor]:
    """Read and validate the hand-curated anchor rows used for scoring."""

    anchor_id = _validated_table_id(project, dataset, "issue_anchor")
    issue_id = _validated_table_id(project, dataset, "issue")
    query = f"""
        SELECT a.issue_id, i.slug AS issue_slug, a.anchor_index, a.language,
               a.text, a.embedding, a.embedding_model
        FROM `{anchor_id}` a
        JOIN `{issue_id}` i ON i.id = a.issue_id
        ORDER BY i.sort_order, i.slug, a.anchor_index
    """
    rows = client.query(query, location=location).result()
    anchors = [
        IssueAnchor(
            issue_id=str(row["issue_id"]),
            issue_slug=str(row["issue_slug"]),
            anchor_index=int(row["anchor_index"]),
            language=str(row["language"]),
            text=str(row["text"]),
            embedding=np.asarray(row["embedding"], dtype=np.float32),
            embedding_model=str(row["embedding_model"]),
        )
        for row in rows
    ]
    grouped: dict[str, list[IssueAnchor]] = defaultdict(list)
    for anchor in anchors:
        grouped[anchor.issue_id].append(anchor)
        if anchor.embedding.shape != (dimensions,):
            raise ValueError(
                f"Anchor {anchor.issue_slug}:{anchor.anchor_index} has "
                f"shape {anchor.embedding.shape}; expected ({dimensions},)"
            )
        if anchor.embedding_model != EMBEDDING_MODEL:
            raise ValueError(
                f"Anchor {anchor.issue_slug}:{anchor.anchor_index} uses "
                f"{anchor.embedding_model!r}; expected {EMBEDDING_MODEL!r}"
            )
    if len(grouped) != EXPECTED_ISSUE_COUNT:
        raise ValueError(
            f"BigQuery anchors cover {len(grouped)} issues; "
            f"expected {EXPECTED_ISSUE_COUNT}"
        )
    for issue_anchors in grouped.values():
        if len(issue_anchors) != ANCHORS_PER_ISSUE:
            raise ValueError(
                f"Issue {issue_anchors[0].issue_slug!r} has "
                f"{len(issue_anchors)} anchors; expected {ANCHORS_PER_ISSUE}"
            )
        counts = Counter(anchor.language for anchor in issue_anchors)
        if dict(counts) != ANCHOR_LANGUAGE_COUNTS:
            raise ValueError(
                f"Issue {issue_anchors[0].issue_slug!r} has language counts "
                f"{dict(counts)}; expected {ANCHOR_LANGUAGE_COUNTS}"
            )
    return anchors


def sync_missing_tweet_embeddings(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    table: str,
    location: str,
    missing_post_ids: Sequence[str],
    tweet_ids: np.ndarray,
    embeddings: np.ndarray,
    dimensions: int,
) -> int:
    """Backfill only empty BigQuery tweet embeddings from the local NPZ."""

    if embeddings.shape != (len(tweet_ids), dimensions):
        raise ValueError("Tweet embedding store has an unexpected shape")
    index_by_id = {str(tweet_id): index for index, tweet_id in enumerate(tweet_ids)}
    missing_locally = [
        post_id for post_id in missing_post_ids if post_id not in index_by_id
    ]
    if missing_locally:
        raise ValueError(
            f"{len(missing_locally)} DB posts are absent from the local "
            f"embedding store; first missing ID: {missing_locally[0]}"
        )
    if not missing_post_ids:
        return 0

    staging_id = _validated_table_id(
        project,
        dataset,
        "stg_tweet_embeddings",
    )
    target_id = _validated_table_id(project, dataset, table)
    rows = (
        {
            "post_id": post_id,
            "embedding": embeddings[index_by_id[post_id]].astype(float).tolist(),
        }
        for post_id in missing_post_ids
    )
    _batch_load_jsonl(
        client=client,
        table_id=staging_id,
        rows=rows,
        schema=[
            bigquery.SchemaField("post_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
        ],
        location=location,
    )
    merge = f"""
        MERGE `{target_id}` t
        USING `{staging_id}` s
        ON t.id = s.post_id
        WHEN MATCHED
          AND COALESCE(ARRAY_LENGTH(t.embedding), 0) = 0
          AND ARRAY_LENGTH(s.embedding) = {dimensions}
        THEN UPDATE SET embedding = s.embedding
    """
    job = client.query(merge, location=location)
    job.result()
    return int(job.num_dml_affected_rows or 0)


def fetch_existing_post_issue_pairs(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    location: str,
    model_version: str | None = None,
) -> set[tuple[str, str]]:
    """Read natural keys already scored by the requested model version."""

    table_id = _validated_table_id(project, dataset, "post_issue")
    where = ""
    job_config = None
    if model_version is not None:
        where = " WHERE model_version = @model_version"
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "model_version",
                    "STRING",
                    model_version,
                )
            ]
        )
    rows = client.query(
        f"SELECT post_id, issue_id FROM `{table_id}`{where}",
        location=location,
        job_config=job_config,
    ).result()
    return {(str(row["post_id"]), str(row["issue_id"])) for row in rows}


def _unit_rows(matrix: np.ndarray, *, label: str) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if not np.isfinite(matrix).all() or np.any(norms == 0):
        raise ValueError(f"{label} contains invalid embedding rows")
    return matrix / norms


def _softmax(values: np.ndarray, *, temperature: float) -> np.ndarray:
    if temperature <= 0:
        raise ValueError("softmax temperature must be greater than zero")
    scaled = np.asarray(values, dtype=np.float64) / temperature
    scaled -= scaled.max(axis=1, keepdims=True)
    exponentials = np.exp(scaled)
    return exponentials / exponentials.sum(axis=1, keepdims=True)


def normalized_text_length(text: str) -> int:
    """Count visible characters after removing URLs and normalizing whitespace."""

    without_urls = _URL_PATTERN.sub("", text)
    return len(" ".join(without_urls.split()))


def _nearest_cluster_ids(
    tweet_embeddings: np.ndarray,
    cluster_centroids: np.ndarray,
) -> np.ndarray:
    """Assign normalized tweets using K-means squared Euclidean distance."""

    vectors = _unit_rows(tweet_embeddings, label="Tweet embeddings")
    centroids = np.asarray(cluster_centroids, dtype=np.float32)
    if (
        centroids.ndim != 2
        or centroids.shape[1] != vectors.shape[1]
        or not np.isfinite(centroids).all()
    ):
        raise ValueError("Tweet cluster centroids have an invalid shape or values")
    squared_distances = (
        np.sum(vectors * vectors, axis=1, keepdims=True)
        + np.sum(centroids * centroids, axis=1)[None, :]
        - 2.0 * vectors @ centroids.T
    )
    return np.argmin(squared_distances, axis=1)


def compute_missing_issue_scores(
    *,
    live_post_ids: Sequence[str],
    post_text_by_id: Mapping[str, str],
    tweet_ids: np.ndarray,
    tweet_embeddings: np.ndarray,
    issues: Sequence[Issue],
    anchors: Sequence[IssueAnchor],
    cluster_centroids: np.ndarray,
    garbage_cluster_ids: set[int] | frozenset[int],
    existing_pairs: set[tuple[str, str]],
    temperature: float = DEFAULT_SOFTMAX_TEMPERATURE,
    cluster_model_version: str = DEFAULT_CLUSTER_MODEL_VERSION,
    batch_size: int = 256,
    show_progress: bool = False,
) -> list[dict[str, Any]]:
    """Calculate gated max-anchor softmax scores for missing model-version rows."""

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if len(issues) != EXPECTED_ISSUE_COUNT:
        raise ValueError(f"Expected {EXPECTED_ISSUE_COUNT} issues, got {len(issues)}")
    tweet_index = {str(tweet_id): index for index, tweet_id in enumerate(tweet_ids)}
    missing_embeddings = [
        post_id for post_id in live_post_ids if post_id not in tweet_index
    ]
    if missing_embeddings:
        raise ValueError(
            f"{len(missing_embeddings)} live posts have no local embedding; "
            f"first missing ID: {missing_embeddings[0]}"
        )
    missing_text = [
        post_id for post_id in live_post_ids if post_id not in post_text_by_id
    ]
    if missing_text:
        raise ValueError(
            f"{len(missing_text)} live posts have no source text; "
            f"first missing ID: {missing_text[0]}"
        )
    cluster_count = len(cluster_centroids)
    invalid_garbage_ids = sorted(
        cluster_id
        for cluster_id in garbage_cluster_ids
        if not 0 <= cluster_id < cluster_count
    )
    if invalid_garbage_ids:
        raise ValueError(
            f"Garbage cluster IDs are outside 0..{cluster_count - 1}: "
            f"{invalid_garbage_ids}"
        )

    anchors_by_issue: dict[str, list[IssueAnchor]] = defaultdict(list)
    for anchor in anchors:
        anchors_by_issue[anchor.issue_id].append(anchor)
    ordered_anchor_rows: list[np.ndarray] = []
    for issue in issues:
        issue_anchors = sorted(
            anchors_by_issue[issue.issue_id],
            key=lambda row: row.anchor_index,
        )
        if len(issue_anchors) != ANCHORS_PER_ISSUE:
            raise ValueError(
                f"Issue {issue.slug!r} does not have {ANCHORS_PER_ISSUE} anchors"
            )
        ordered_anchor_rows.extend(anchor.embedding for anchor in issue_anchors)
    anchor_matrix = _unit_rows(
        np.asarray(ordered_anchor_rows, dtype=np.float32),
        label="Issue anchors",
    )

    posts_to_score = [
        post_id
        for post_id in live_post_ids
        if any((post_id, issue.issue_id) not in existing_pairs for issue in issues)
    ]
    output: list[dict[str, Any]] = []
    starts: Iterable[int] = range(0, len(posts_to_score), batch_size)
    if show_progress:
        starts = tqdm(
            starts,
            total=(len(posts_to_score) + batch_size - 1) // batch_size,
            desc="Calculating issue scores",
            unit="batch",
            dynamic_ncols=True,
        )
    for start in starts:
        batch_ids = posts_to_score[start : start + batch_size]
        batch_vectors = _unit_rows(
            np.asarray(
                [tweet_embeddings[tweet_index[post_id]] for post_id in batch_ids],
                dtype=np.float32,
            ),
            label="Tweet embeddings",
        )
        cluster_ids = _nearest_cluster_ids(batch_vectors, cluster_centroids)
        similarities = batch_vectors @ anchor_matrix.T
        max_by_issue = similarities.reshape(
            len(batch_ids),
            len(issues),
            ANCHORS_PER_ISSUE,
        ).max(axis=2)
        probabilities = _softmax(max_by_issue, temperature=temperature)
        for post_offset, post_id in enumerate(batch_ids):
            should_zero = (
                normalized_text_length(post_text_by_id[post_id])
                < MIN_SCORABLE_TEXT_LENGTH
                or int(cluster_ids[post_offset]) in garbage_cluster_ids
            )
            for issue_offset, issue in enumerate(issues):
                if (post_id, issue.issue_id) in existing_pairs:
                    continue
                output.append(
                    {
                        "post_id": post_id,
                        "issue_id": issue.issue_id,
                        "confidence": (
                            0.0
                            if should_zero
                            else round(
                                float(
                                    probabilities[post_offset, issue_offset]
                                ),
                                9,
                            )
                        ),
                        "model_version": issue_score_model_version(
                            temperature,
                            cluster_model_version=cluster_model_version,
                        ),
                    }
                )
    return output


def upload_issue_scores(
    *,
    client: bigquery.Client,
    project: str,
    dataset: str,
    location: str,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    """Upsert versioned post-issue scores and keep promise flags false."""

    if not rows:
        return 0
    staging_id = _validated_table_id(
        project,
        dataset,
        "stg_post_issue_scores",
    )
    target_id = _validated_table_id(project, dataset, "post_issue")
    _batch_load_jsonl(
        client=client,
        table_id=staging_id,
        rows=rows,
        schema=[
            bigquery.SchemaField("post_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("issue_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("confidence", "NUMERIC", mode="REQUIRED"),
            bigquery.SchemaField("model_version", "STRING", mode="REQUIRED"),
        ],
        location=location,
    )
    merge = f"""
        MERGE `{target_id}` t
        USING `{staging_id}` s
        ON t.post_id = s.post_id AND t.issue_id = s.issue_id
        WHEN MATCHED THEN UPDATE SET
          confidence = s.confidence,
          is_concrete_promise = FALSE,
          model_version = s.model_version
        WHEN NOT MATCHED THEN INSERT (
          id, post_id, issue_id, confidence, is_concrete_promise,
          model_version
        )
        VALUES (
          GENERATE_UUID(), s.post_id, s.issue_id, s.confidence, FALSE,
          s.model_version
        )
    """
    job = client.query(merge, location=location)
    job.result()
    return int(job.num_dml_affected_rows or 0)
