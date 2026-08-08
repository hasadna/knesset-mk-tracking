"""Vertex AI service for Gemini generation and embeddings."""

from __future__ import annotations

import math
import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_EMBEDDING_DIMENSIONS = 3072

T = TypeVar("T")
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class GeneratedText:
    """Generated text and the billable token counts reported by Vertex AI."""

    text: str
    input_tokens: int
    output_tokens: int


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
