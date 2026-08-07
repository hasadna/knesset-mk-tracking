from __future__ import annotations

import json
from pathlib import Path

import pytest

from mk_tracking.pipeline import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    VertexAIService,
    embed_subjects,
    embed_tweets,
    transform_tweet_exports,
)


class FakeService:
    embedding_model = EMBEDDING_MODEL
    output_dimensions = 2

    def embed_text(self, text: str, *, role: str) -> list[float]:
        assert role in {"query", "passage"}
        return [float(len(text)), 1.0]


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_transform_preserves_raw_tweet_fields_and_adds_source_metadata(
    tmp_path: Path,
) -> None:
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir()
    payload = {
        "account": "ExampleMK",
        "user_id": "123",
        "fetched_at": "2026-07-30T12:00:00Z",
        "tweets_count": 1,
        "tweets": [
            {
                "id": "999",
                "author_id": "123",
                "created_at": "2026-07-29T10:30:00Z",
                "text": "ציוץ לדוגמה",
                "entities": {"hashtags": [{"tag": "דוגמה"}]},
                "public_metrics": {"like_count": 20},
            }
        ],
        "raw_user_response": {
            "data": {"id": "123", "name": "Example", "username": "ExampleMK"}
        },
    }
    (raw_directory / "export.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "tweets.jsonl"

    transform_tweet_exports(raw_directory, output)

    rows = read_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["id"] == "999"
    assert rows[0]["entities"] == {"hashtags": [{"tag": "דוגמה"}]}
    assert rows[0]["public_metrics"] == {"like_count": 20}
    assert rows[0]["post_id"] == "999"
    assert rows[0]["post_text"] == "ציוץ לדוגמה"
    assert rows[0]["source_file"] == "export.json"
    assert rows[0]["author_username"] == "ExampleMK"
    assert rows[0]["tweet_url"] == "https://x.com/ExampleMK/status/999"


def test_embedded_tweet_table_contains_every_input_field(tmp_path: Path) -> None:
    input_path = tmp_path / "tweets.jsonl"
    source_row = {
        "post_id": "999",
        "post_text": "ציוץ לדוגמה",
        "nested": {"preserved": True},
    }
    input_path.write_text(
        json.dumps(source_row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "tweets_with_embeddings.jsonl"

    embed_tweets(input_path, output_path, service=FakeService())

    row = read_jsonl(output_path)[0]
    for key, value in source_row.items():
        assert row[key] == value
    assert row["embedding_model"] == EMBEDDING_MODEL
    assert row["embedding_dimensions"] == 2
    assert row["embedding"] == [11.0, 1.0]


def test_tweet_embedding_resumes_without_repeating_completed_rows(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tweets.jsonl"
    rows = [
        {"post_id": "one", "post_text": "ראשון"},
        {"post_id": "two", "post_text": "שני"},
    ]
    input_path.write_text(
        "".join(
            f"{json.dumps(row, ensure_ascii=False)}\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "tweets_with_embeddings.jsonl"
    service = FakeService()
    first_embedded = {
        **rows[0],
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": 2,
        "embedding": [5.0, 1.0],
    }
    output_path.write_text(
        json.dumps(first_embedded, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    embed_tweets(input_path, output_path, service=service)

    embedded = read_jsonl(output_path)
    assert len(embedded) == 2
    assert embedded[0] == first_embedded
    assert embedded[1]["post_id"] == "two"


def test_tweet_embedding_skips_a_complete_output(tmp_path: Path) -> None:
    input_path = tmp_path / "tweets.jsonl"
    row = {"post_id": "one", "post_text": "ראשון"}
    input_path.write_text(
        json.dumps(row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "tweets_with_embeddings.jsonl"
    completed = {
        **row,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": 2,
        "embedding": [5.0, 1.0],
    }
    output_path.write_text(
        json.dumps(completed, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    messages: list[str] = []

    embed_tweets(
        input_path,
        output_path,
        service=FakeService(),
        progress=messages.append,
    )

    assert read_jsonl(output_path) == [completed]
    assert messages == [
        "Tweet embeddings: output already contains all 1 embeddings; skipping"
    ]


def test_subject_table_has_six_knn_anchor_rows(
    tmp_path: Path,
) -> None:
    subjects_path = tmp_path / "subjects.json"
    subjects_path.write_text(
        json.dumps(
            [
                {
                    "subject_id": "economy",
                    "label_he": "כלכלה",
                    "description_he": "מדיניות כלכלית ותקציב המדינה",
                    "representative_sentences_he": [
                        f"משפט מייצג {index}" for index in range(1, 6)
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "subjects_with_embeddings.jsonl"

    embed_subjects(subjects_path, output_path, service=FakeService())

    rows = read_jsonl(output_path)
    assert len(rows) == 6
    assert {row["subject_id"] for row in rows} == {"economy"}
    assert rows[0]["anchor_type"] == "subject_description"
    assert rows[0]["anchor_index"] == 0
    assert rows[1]["anchor_type"] == "representative_sentence"
    assert rows[1]["anchor_index"] == 1
    assert [row["anchor_index"] for row in rows[1:]] == [1, 2, 3, 4, 5]
    assert all(row["embedding"] for row in rows)
    assert all(row["embedding_model"] == EMBEDDING_MODEL for row in rows)


def test_builtin_catalog_has_15_subjects_and_75_curated_sentences(
    tmp_path: Path,
) -> None:
    catalog_path = Path(__file__).parents[1] / "data" / "subjects.json"
    subjects = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert len(subjects) == 15
    sentence_count = sum(
        len(subject["representative_sentences_he"]) for subject in subjects
    )
    assert sentence_count == 75
    assert all(
        len(set(subject["representative_sentences_he"])) == 5
        for subject in subjects
    )

    output_path = tmp_path / "subjects_with_embeddings.jsonl"
    embed_subjects(catalog_path, output_path, service=FakeService())
    rows = read_jsonl(output_path)

    assert len(rows) == 90
    assert sum(row["anchor_type"] == "subject_description" for row in rows) == 15
    assert sum(row["anchor_type"] == "representative_sentence" for row in rows) == 75


class FakeEmbeddingModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def embed_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        statistics = type("Statistics", (), {"token_count": 12.0})()
        embedding = type(
            "Embedding",
            (),
            {"values": [3.0, 4.0], "statistics": statistics},
        )()
        return type("Response", (), {"embeddings": [embedding]})()


class FakeVertexClient:
    def __init__(self) -> None:
        self.models = FakeEmbeddingModels()


def test_vertex_service_uses_gemini_embedding_2_and_normalizes() -> None:
    client = FakeVertexClient()
    service = VertexAIService(
        client=client,
        output_dimensions=2,
        max_retries=0,
    )

    vector = service.embed_text("טקסט", role="passage")

    assert vector == pytest.approx([0.6, 0.8])
    call = client.models.calls[0]
    assert call["model"] == "gemini-embedding-2"
    contents = call["contents"]
    assert isinstance(contents, list)
    assert contents[0].parts[0].text == "title: none | text: טקסט"
    assert call["config"].output_dimensionality == 2
    assert service.last_embedding_input_tokens == 12


class FakeGenerationModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        usage = type(
            "Usage",
            (),
            {
                "prompt_token_count": 100,
                "candidates_token_count": 20,
                "thoughts_token_count": 5,
            },
        )()
        return type(
            "Response",
            (),
            {
                "text": '{"title_he":"כותרת","meaning_he":"משמעות"}',
                "usage_metadata": usage,
            },
        )()


class FakeGenerationClient:
    def __init__(self) -> None:
        self.models = FakeGenerationModels()


def test_vertex_service_generation_returns_reported_usage() -> None:
    client = FakeGenerationClient()
    service = VertexAIService(client=client, max_retries=0)

    generated = service.generate_text(
        "סכם את האשכול",
        model="gemini-2.5-flash",
        max_output_tokens=200,
        thinking_budget=0,
        response_mime_type="application/json",
        response_schema={"type": "OBJECT"},
    )

    assert generated.text == '{"title_he":"כותרת","meaning_he":"משמעות"}'
    assert generated.input_tokens == 100
    assert generated.output_tokens == 25
    call = client.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    assert call["config"].thinking_config.thinking_budget == 0


def test_vertex_service_rejects_invalid_dimensions() -> None:
    with pytest.raises(ValueError, match="between 1 and 3072"):
        VertexAIService(output_dimensions=0)


def test_vertex_service_defaults_to_full_embedding_dimensions() -> None:
    service = VertexAIService()

    assert DEFAULT_EMBEDDING_DIMENSIONS == 3072
    assert service.output_dimensions == 3072
