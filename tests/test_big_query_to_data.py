from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from mk_tracking.vertex import EMBEDDING_MODEL

SCRIPT_PATH = Path(__file__).parents[1] / "src" / "mk_tracking" / "process_embeddings" / "big_query_to_data.py"
SPEC = importlib.util.spec_from_file_location("big_query_to_data", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

GeneratedText = MODULE.GeneratedText
DEFAULT_K = MODULE.DEFAULT_K
DEFAULT_N_INIT = MODULE.DEFAULT_N_INIT
load_cluster_flags = MODULE.load_cluster_flags
load_embedding_store = MODULE.load_embedding_store
parse_summary = MODULE._parse_summary
run_pipeline = MODULE.run_pipeline


class FakeQueryJob:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows
        self.total_bytes_processed = 1234
        self.total_bytes_billed = 10_000_000
        self.cache_hit = False

    def result(self) -> list[dict[str, str]]:
        return self._rows


class FakeBigQueryClient:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query(self, query: str, **_: object) -> FakeQueryJob:
        self.queries.append(query)
        return FakeQueryJob(self.rows)


class FakeAIService:
    embedding_model = EMBEDDING_MODEL
    output_dimensions = 2

    def __init__(self) -> None:
        self.last_embedding_input_tokens: int | None = None
        self.embedded_texts: list[str] = []
        self.summary_prompts: list[str] = []

    def embed_text(self, text: str, *, role: str) -> list[float]:
        assert role == "passage"
        self.embedded_texts.append(text)
        self.last_embedding_input_tokens = 10
        if text.startswith("א"):
            return [1.0, 0.1]
        return [0.1, 1.0]

    def generate_text(self, prompt: str, **_: object) -> GeneratedText:
        self.summary_prompts.append(prompt)
        index = len(self.summary_prompts)
        return GeneratedText(
            text=json.dumps(
                {
                    "title_he": f"כותרת {index}",
                    "meaning_he": f"משמעות {index}",
                    "is_garbage": index % 2 == 0,
                    "garbage_reason_he": f"סיבת סיווג {index}",
                },
                ensure_ascii=False,
            ),
            input_tokens=100,
            output_tokens=20,
        )


def _source_rows(count: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index in range(count):
        prefix = "א" if index < count // 2 else "ב"
        rows.append(
            {
                "id": f"id-{index:02d}",
                "text": f"{prefix} ציוץ מספר {index}",
            }
        )
    return rows


def _history(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_k30_automatic_garbage_model_is_versioned() -> None:
    flags = load_cluster_flags(
        Path(__file__).parents[1] / "data" / "automatic" / "tweet_cluster_flags.json"
    )

    assert flags.k == DEFAULT_K == 30
    assert flags.n_init == DEFAULT_N_INIT == 1
    assert flags.random_state == 42
    assert flags.garbage_cluster_ids == frozenset()
    assert flags.model_version.endswith("auto-garbage:v3")


def test_empty_garbage_reason_gets_a_deterministic_fallback() -> None:
    result = parse_summary(
        GeneratedText(
            text=json.dumps(
                {
                    "title_he": "כותרת",
                    "meaning_he": "משמעות",
                    "is_garbage": True,
                    "garbage_reason_he": "",
                },
                ensure_ascii=False,
            ),
            input_tokens=10,
            output_tokens=5,
        )
    )

    assert result[:3] == ("כותרת", "משמעות", True)
    assert result[3]


def test_pipeline_updates_embeddings_but_keeps_existing_centroids(
    tmp_path: Path,
) -> None:
    embedding_file = tmp_path / "tweet_embeddings.npz"
    centroid_file = tmp_path / "centroids.npz"
    history_file = tmp_path / "history.jsonl"
    first_service = FakeAIService()

    first = run_pipeline(
        project="valid-project-123",
        dataset="dataset",
        table="social_post",
        bigquery_location="US",
        embedding_file=embedding_file,
        centroid_file=centroid_file,
        history_file=history_file,
        checkpoint_every=2,
        k=2,
        sample_n=2,
        random_state=42,
        sync_database=False,
        bigquery_client=FakeBigQueryClient(_source_rows(6)),
        ai_service=first_service,
    )

    assert first.tweets_processed == 6
    assert first.centroids_updated is True
    assert len(first_service.summary_prompts) == 2
    centroid_bytes = centroid_file.read_bytes()

    second_service = FakeAIService()
    second = run_pipeline(
        project="valid-project-123",
        dataset="dataset",
        table="social_post",
        bigquery_location="US",
        embedding_file=embedding_file,
        centroid_file=centroid_file,
        history_file=history_file,
        checkpoint_every=2,
        k=2,
        sample_n=2,
        random_state=42,
        sync_database=False,
        bigquery_client=FakeBigQueryClient(_source_rows(7)),
        ai_service=second_service,
    )

    assert second.tweets_processed == 1
    assert second.centroids_updated is False
    assert len(second_service.embedded_texts) == 1
    assert second_service.summary_prompts == []
    assert centroid_file.read_bytes() == centroid_bytes

    tweet_ids, embeddings = load_embedding_store(
        embedding_file,
        dimensions=2,
    )
    assert len(tweet_ids) == 7
    assert embeddings.shape == (7, 2)
    assert embeddings.dtype == np.float32

    records = _history(history_file)
    assert len(records) == 2
    assert records[0]["tweets_processed"] == 6
    assert records[0]["centroids_updated"] is True
    assert records[0]["api_usage"]["summarization_requests"] == 2
    assert records[0]["api_cost_usd"]["total_cost_usd"] > 0
    assert records[1]["tweets_processed"] == 1
    assert records[1]["centroids_updated"] is False
    assert records[1]["api_usage"]["summarization_requests"] == 0
    assert records[1]["api_cost_usd"]["total_cost_usd"] > 0

    with np.load(centroid_file, allow_pickle=False) as centroids:
        assert centroids["centroid"].shape == (2, 2)
        assert centroids["meaning_he"].tolist() == ["משמעות 1", "משמעות 2"]
        assert centroids["title_he"].tolist() == ["כותרת 1", "כותרת 2"]
        assert centroids["is_garbage"].tolist() == [False, True]
        assert centroids["garbage_reason_he"].tolist() == [
            "סיבת סיווג 1",
            "סיבת סיווג 2",
        ]
        assert str(centroids["cluster_model_version"]) == ""
        assert centroids["cluster_size"].sum() == 6
        sampled_ids = [
            tweet_id
            for tweet_id in centroids["sample_tweet_id"].ravel().tolist()
            if tweet_id
        ]
        assert len(sampled_ids) == 4
        assert all(tweet_id.startswith("id-") for tweet_id in sampled_ids)
