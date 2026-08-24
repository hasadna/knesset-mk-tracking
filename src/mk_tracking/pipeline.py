"""Transform Twitter exports and create Gemini embeddings."""

# TODO(postgres-migration): the Google Cloud inference APIs (Vertex AI / Gemini
# embeddings and summarisation) are deliberately OUT OF SCOPE for the PostgreSQL
# migration and are left as they are. Only the data store moved; the model calls
# did not. Revisit separately.

from __future__ import annotations

import json
import math
import os
import random
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_EMBEDDING_DIMENSIONS = 3072
REPRESENTATIVE_SENTENCE_COUNT = 5

JsonObject = dict[str, Any]
T = TypeVar("T")
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class GeneratedText:
    """Generated text and the billable token counts reported by Vertex AI."""

    text: str
    input_tokens: int
    output_tokens: int


class EmbeddingService(Protocol):
    """Operations needed by the file pipeline."""

    embedding_model: str
    output_dimensions: int

    def embed_text(self, text: str, *, role: str) -> list[float]:
        """Return one normalized embedding."""


def _read_json_object(path: Path) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read JSON file {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _read_jsonl(path: str | Path) -> list[JsonObject]:
    table_path = Path(path)
    rows: list[JsonObject] = []
    try:
        with table_path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Expected an object at {table_path}:{line_number}"
                    )
                rows.append(value)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read JSONL table {table_path}: {error}") from error
    return rows


def _write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """Atomically replace a JSONL table."""

    table_path = Path(path)
    table_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = table_path.with_suffix(f"{table_path.suffix}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")
        temporary_path.replace(table_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return table_path


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        file.write("\n")


def _embed_rows(
    rows: Sequence[JsonObject],
    output_path: str | Path,
    *,
    service: EmbeddingService,
    text_column: str,
    role: str,
    label: str,
    progress: ProgressCallback | None,
    progress_every: int,
) -> Path:
    """Embed rows with a restart-safe, prefix-validated checkpoint table."""

    if progress_every < 1:
        raise ValueError("progress_every must be at least 1")
    table_path = Path(output_path)
    existing = _read_jsonl(table_path) if table_path.exists() else []
    if len(existing) > len(rows):
        raise ValueError(f"Existing output has more rows than input: {table_path}")

    for index, embedded in enumerate(existing):
        source = rows[index]
        if any(embedded.get(key) != value for key, value in source.items()):
            raise ValueError(
                f"Existing output does not match input row {index + 1}: {table_path}"
            )
        if (
            embedded.get("embedding_model") != service.embedding_model
            or embedded.get("embedding_dimensions") != service.output_dimensions
        ):
            raise ValueError(
                f"Existing output uses different embedding settings: {table_path}"
            )
        vector = embedded.get("embedding")
        if not isinstance(vector, list) or len(vector) != service.output_dimensions:
            raise ValueError(f"Invalid embedding in existing output row {index + 1}")

    total = len(rows)
    if len(existing) == total:
        if progress:
            progress(
                f"{label}: output already contains all {total} embeddings; skipping"
            )
        return table_path

    if not existing and not table_path.exists():
        table_path.parent.mkdir(parents=True, exist_ok=True)
        table_path.touch()

    if progress:
        if existing:
            progress(
                f"{label}: found {len(existing)}/{total} completed rows; "
                f"resuming at row {len(existing) + 1}"
            )
        else:
            progress(f"{label}: embedding {total} rows")

    started_at = time.monotonic()
    initial_count = len(existing)
    for index in range(len(existing), len(rows)):
        source = rows[index]
        text = source.get(text_column)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Row {index + 1} has no {text_column}")
        embedded = dict(source)
        embedded["embedding_model"] = service.embedding_model
        embedded["embedding_dimensions"] = service.output_dimensions
        embedded["embedding"] = service.embed_text(text, role=role)
        _append_jsonl(table_path, embedded)
        completed = index + 1
        completed_this_run = completed - initial_count
        if progress and (
            completed == total
            or completed_this_run == 1
            or completed_this_run % progress_every == 0
        ):
            elapsed = max(time.monotonic() - started_at, 0.001)
            rows_per_second = completed_this_run / elapsed
            remaining_seconds = (total - completed) / rows_per_second
            progress(
                f"{label}: {completed}/{total} ({completed / total:.1%}); "
                f"ETA {remaining_seconds / 60:.1f} min"
            )

    return table_path


def _source_metadata(payload: Mapping[str, Any], path: Path) -> JsonObject:
    return {
        "source_file": path.name,
        "source_account": payload.get("account"),
        "source_user_id": payload.get("user_id"),
        "source_fetched_at": payload.get("fetched_at"),
        "source_tweets_count": payload.get("tweets_count"),
        "source_user_response": payload.get("raw_user_response"),
    }


def transform_tweet_exports(
    input_directory: str | Path,
    output_path: str | Path,
) -> Path:
    """Convert collector-wrapped JSON exports into one-row-per-tweet JSONL.

    Every original tweet field is retained. Source account metadata and the
    normalized ``post_id``/``post_text`` aliases used by the embedding stage are
    added to each row.
    """

    directory = Path(input_directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"Tweet export directory does not exist: {directory}")
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No JSON exports found in: {directory}")

    rows: list[JsonObject] = []
    seen_ids: set[str] = set()
    for path in paths:
        payload = _read_json_object(path)
        tweets = payload.get("tweets", [])
        if not isinstance(tweets, list):
            raise ValueError(f"'tweets' must be an array in {path}")
        source = _source_metadata(payload, path)
        user_response = payload.get("raw_user_response")
        user = (
            user_response.get("data", {})
            if isinstance(user_response, dict)
            else {}
        )
        username = (
            str(user.get("username") or payload.get("account") or "").strip()
            if isinstance(user, dict)
            else str(payload.get("account") or "").strip()
        )

        for index, tweet in enumerate(tweets):
            if not isinstance(tweet, dict):
                raise ValueError(f"Tweet {index} in {path} must be an object")
            tweet_id = str(tweet.get("id") or "").strip()
            text = tweet.get("text")
            if not tweet_id or not isinstance(text, str) or not text.strip():
                raise ValueError(f"Tweet {index} in {path} has no id or text")
            if tweet_id in seen_ids:
                raise ValueError(f"Duplicate tweet id {tweet_id!r}")
            seen_ids.add(tweet_id)

            row = dict(tweet)
            row.update(source)
            row["post_id"] = tweet_id
            row["post_text"] = text
            row["author_username"] = username or None
            row["tweet_url"] = (
                f"https://x.com/{username}/status/{tweet_id}"
                if username
                else f"https://x.com/i/status/{tweet_id}"
            )
            rows.append(row)

    return _write_jsonl(output_path, rows)


def _normalized(vector: Sequence[float]) -> list[float]:
    if not vector:
        raise ValueError("Vertex AI returned an empty embedding")
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Vertex AI returned a non-finite embedding")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError("Vertex AI returned a zero-length embedding")
    return [value / norm for value in values]


class VertexAIService:
    """Gemini generation and ``gemini-embedding-2`` through Vertex AI."""

    def __init__(
        self,
        *,
        project: str | None = None,
        location: str = "global",
        output_dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
        max_retries: int = 6,
        client: object | None = None,
        sleep: Callable[[float], None] = time.sleep,
        progress: ProgressCallback | None = None,
    ) -> None:
        if not 1 <= output_dimensions <= 3072:
            raise ValueError("output_dimensions must be between 1 and 3072")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location
        self.embedding_model = EMBEDDING_MODEL
        self.output_dimensions = output_dimensions
        self.max_retries = max_retries
        self._client = client
        self._sleep = sleep
        self._progress = progress
        self.last_embedding_input_tokens: int | None = None

    def _load_client(self) -> object:
        if self._client is None:
            if not self.project:
                raise ValueError(
                    "Set GOOGLE_CLOUD_PROJECT or pass project= before calling Vertex AI"
                )
            from google import genai

            self._client = genai.Client(
                vertexai=True,
                project=self.project,
                location=self.location,
                http_options={"api_version": "v1"},
            )
        return self._client

    def _with_retry(self, operation: Callable[[], T]) -> T:
        for attempt in range(self.max_retries + 1):
            try:
                return operation()
            except Exception as error:
                code = getattr(error, "code", None)
                retryable = code in {429, 500, 502, 503, 504}
                if not retryable or attempt == self.max_retries:
                    raise
                delay = min(60.0, (2**attempt) + random.random())
                if self._progress:
                    self._progress(
                        f"Vertex AI error {code}; retry "
                        f"{attempt + 1}/{self.max_retries} in {delay:.1f}s"
                    )
                self._sleep(delay)
        raise AssertionError("unreachable")

    def embed_text_with_usage(
        self,
        text: str,
        *,
        role: str,
    ) -> tuple[list[float], int]:
        """Embed text and return its vector and request-local token usage."""
        stripped = text.strip()
        if not stripped:
            raise ValueError("Cannot embed empty text")
        if role not in {"query", "passage"}:
            raise ValueError("role must be 'query' or 'passage'")

        from google.genai import types

        prepared = (
            f"task: search result | query: {stripped}"
            if role == "query"
            else f"title: none | text: {stripped}"
        )
        content = types.Content(parts=[types.Part.from_text(text=prepared)])

        def request() -> object:
            client = self._load_client()
            return client.models.embed_content(  # type: ignore[attr-defined]
                model=self.embedding_model,
                contents=[content],
                config=types.EmbedContentConfig(
                    output_dimensionality=self.output_dimensions
                ),
            )

        response = self._with_retry(request)
        embeddings = getattr(response, "embeddings", None)
        if not embeddings or len(embeddings) != 1:
            raise ValueError("Vertex AI returned an unexpected embedding response")
        embedding = embeddings[0]
        values = getattr(embedding, "values", None)
        statistics = getattr(embedding, "statistics", None)
        token_count = getattr(statistics, "token_count", None)
        if token_count is None:
            raise ValueError("Vertex AI did not return embedding token usage")
        return _normalized(values or []), round(float(token_count))

    def embed_text(self, text: str, *, role: str) -> list[float]:
        """Embed one query or passage and normalize its vector."""

        vector, token_count = self.embed_text_with_usage(text, role=role)
        self.last_embedding_input_tokens = token_count
        return vector

    def generate_text(
        self,
        prompt: str,
        *,
        model: str,
        max_output_tokens: int = 512,
        temperature: float = 0.2,
        thinking_budget: int | None = 0,
        response_mime_type: str | None = None,
        response_schema: Mapping[str, Any] | None = None,
    ) -> GeneratedText:
        """Generate text and return Vertex AI's billable token counts."""

        stripped = prompt.strip()
        if not stripped:
            raise ValueError("Cannot generate from an empty prompt")
        if not model.strip():
            raise ValueError("model cannot be empty")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")

        from google.genai import types

        config_values: dict[str, Any] = {
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
        }
        if thinking_budget is not None:
            config_values["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget
            )
        if response_mime_type is not None:
            config_values["response_mime_type"] = response_mime_type
        if response_schema is not None:
            config_values["response_schema"] = response_schema

        def request() -> object:
            client = self._load_client()
            return client.models.generate_content(  # type: ignore[attr-defined]
                model=model,
                contents=stripped,
                config=types.GenerateContentConfig(**config_values),
            )

        response = self._with_retry(request)
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Vertex AI returned an empty generated response")

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None)
        candidate_tokens = getattr(usage, "candidates_token_count", None)
        thought_tokens = getattr(usage, "thoughts_token_count", 0) or 0
        if input_tokens is None or candidate_tokens is None:
            raise ValueError("Vertex AI did not return generation token usage")
        return GeneratedText(
            text=text.strip(),
            input_tokens=int(input_tokens),
            output_tokens=int(candidate_tokens) + int(thought_tokens),
        )


def embed_tweets(
    input_path: str | Path,
    output_path: str | Path,
    *,
    service: EmbeddingService,
    progress: ProgressCallback | None = None,
    progress_every: int = 10,
) -> Path:
    """Copy the tweet table and add a passage embedding to every row.

    Completed rows are reused when the output is a valid prefix of the input, so
    a quota error or interrupted process can resume without repeating API calls.
    """
    rows = _read_jsonl(input_path)
    return _embed_rows(
        rows,
        output_path,
        service=service,
        text_column="post_text",
        role="passage",
        label="Tweet embeddings",
        progress=progress,
        progress_every=progress_every,
    )


def _load_subjects(path: str | Path) -> list[JsonObject]:
    subject_path = Path(path)
    try:
        value = json.loads(subject_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read subjects {subject_path}: {error}") from error
    if not isinstance(value, list) or not value:
        raise ValueError("Subjects file must contain a non-empty JSON array")

    subjects: list[JsonObject] = []
    seen_ids: set[str] = set()
    for index, subject in enumerate(value, start=1):
        if not isinstance(subject, dict):
            raise ValueError(f"Subject {index} must be an object")
        subject_id = subject.get("subject_id")
        label = subject.get("label_he")
        description = subject.get("description_he")
        representative_sentences = subject.get("representative_sentences_he")
        if not all(
            isinstance(item, str) and item.strip()
            for item in (subject_id, label, description)
        ):
            raise ValueError(
                f"Subject {index} needs subject_id, label_he, and description_he"
            )
        if (
            not isinstance(representative_sentences, list)
            or len(representative_sentences) != REPRESENTATIVE_SENTENCE_COUNT
            or any(
                not isinstance(sentence, str) or not sentence.strip()
                for sentence in representative_sentences
            )
        ):
            raise ValueError(
                f"Subject {subject_id!r} needs exactly five representative_sentences_he"
            )
        if len(set(representative_sentences)) != REPRESENTATIVE_SENTENCE_COUNT:
            raise ValueError(
                f"Subject {subject_id!r} has duplicate representative sentences"
            )
        if subject_id in seen_ids:
            raise ValueError(f"Duplicate subject_id {subject_id!r}")
        seen_ids.add(subject_id)
        subjects.append(subject)
    return subjects


def embed_subjects(
    subjects_path: str | Path,
    output_path: str | Path,
    *,
    service: EmbeddingService,
    progress: ProgressCallback | None = None,
    progress_every: int = 5,
) -> Path:
    """Create a flat KNN anchor table with six embedded rows per subject."""

    subjects = _load_subjects(subjects_path)
    anchor_rows: list[JsonObject] = []
    for subject in subjects:
        description = subject["description_he"]
        sentences = subject["representative_sentences_he"]
        anchors = [
            ("subject_description", 0, description),
            *[
                ("representative_sentence", index, sentence)
                for index, sentence in enumerate(sentences, start=1)
            ],
        ]
        subject_columns = {
            key: value
            for key, value in subject.items()
            if key != "representative_sentences_he"
        }
        for anchor_type, anchor_index, anchor_text in anchors:
            anchor_rows.append(
                {
                    **subject_columns,
                    "anchor_type": anchor_type,
                    "anchor_index": anchor_index,
                    "anchor_text": anchor_text,
                }
            )
    return _embed_rows(
        anchor_rows,
        output_path,
        service=service,
        text_column="anchor_text",
        role="query",
        label="Subject anchors",
        progress=progress,
        progress_every=progress_every,
    )
