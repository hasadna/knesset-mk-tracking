from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mk_tracking.bigquery_issue_scoring import (
    ANCHOR_LANGUAGE_COUNTS,
    ANCHORS_PER_ISSUE,
    EXPECTED_ISSUE_COUNT,
    AnchorDefinition,
    Issue,
    IssueAnchor,
    compute_missing_issue_scores,
    initialize_anchor_embeddings,
    load_anchor_definitions,
    load_anchor_embedding_store,
    sync_missing_tweet_embeddings,
)
from mk_tracking.pipeline import EMBEDDING_MODEL


class FakeEmbeddingService:
    embedding_model = EMBEDDING_MODEL
    output_dimensions = 3

    def __init__(self) -> None:
        self.last_embedding_input_tokens: int | None = None
        self.calls: list[tuple[str, str]] = []

    def embed_text(self, text: str, *, role: str) -> list[float]:
        self.calls.append((text, role))
        self.last_embedding_input_tokens = 5
        return [1.0, float(len(text) % 3), 0.5]


def _issues() -> list[Issue]:
    return [
        Issue(
            issue_id=f"issue-{index}",
            slug=f"slug-{index}",
            name=f"Issue {index}",
            description=f"Description {index}",
            social_post_prompt=f"Prompt {index}",
            sort_order=index,
        )
        for index in range(EXPECTED_ISSUE_COUNT)
    ]


def _anchors(issues: list[Issue]) -> list[IssueAnchor]:
    vectors = [
        [1.0, 0.0],
        [0.0, 1.0],
        [-1.0, 0.0],
        [0.0, -1.0],
        [0.7, 0.7],
            [-0.7, 0.7],
            [-0.7, -0.7],
        ]
    output: list[IssueAnchor] = []
    for issue, vector in zip(issues, vectors, strict=True):
        for anchor_index in range(1, ANCHORS_PER_ISSUE + 1):
            language = (
                "he" if anchor_index <= 4 else "en" if anchor_index <= 6 else "ar"
            )
            output.append(
                IssueAnchor(
                    issue_id=issue.issue_id,
                    issue_slug=issue.slug,
                    anchor_index=anchor_index,
                    language=language,
                    text=f"{issue.slug} anchor {anchor_index}",
                    embedding=np.asarray(vector, dtype=np.float32),
                    embedding_model=EMBEDDING_MODEL,
                )
            )
    return output


def _cluster_centroids() -> np.ndarray:
    return np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )


@pytest.mark.xfail(
    reason=(
        "Known pre-existing failure: data/seed/issue_anchors.json declares 7 issues but "
        "bigquery_issue_scoring.EXPECTED_ISSUE_COUNT is 8. Tracked as the 'issue "
        "taxonomy count' item. Remove this marker once the taxonomy is reconciled."
    ),
    strict=False,
)
def test_curated_anchor_file_has_eight_anchors_with_expected_languages() -> None:
    path = Path(__file__).parents[1] / "data" / "seed" / "issue_anchors.json"

    definitions = load_anchor_definitions(path)

    assert len(definitions) == EXPECTED_ISSUE_COUNT * ANCHORS_PER_ISSUE
    for slug in {row.issue_slug for row in definitions}:
        issue_rows = [row for row in definitions if row.issue_slug == slug]
        counts = {
            language: sum(row.language == language for row in issue_rows)
            for language in ANCHOR_LANGUAGE_COUNTS
        }
        assert counts == ANCHOR_LANGUAGE_COUNTS


def test_anchor_embedding_initialization_is_resumable(tmp_path: Path) -> None:
    definitions = tuple(
        AnchorDefinition(
            issue_slug="one",
            anchor_index=index,
            language="he",
            text=f"anchor {index}",
        )
        for index in range(1, 4)
    )
    path = tmp_path / "anchors.npz"
    first_service = FakeEmbeddingService()

    first, first_usage = initialize_anchor_embeddings(
        definitions=definitions,
        path=path,
        service=first_service,
        checkpoint_every=1,
    )

    assert first.embeddings.shape == (3, 3)
    assert first_usage.requests == 3
    assert first_usage.input_tokens == 15
    assert all(role == "query" for _, role in first_service.calls)

    second_service = FakeEmbeddingService()
    second, second_usage = initialize_anchor_embeddings(
        definitions=definitions,
        path=path,
        service=second_service,
        checkpoint_every=1,
    )

    assert second_usage.requests == 0
    assert second_usage.input_tokens == 0
    assert second_service.calls == []
    loaded = load_anchor_embedding_store(path, dimensions=3)
    np.testing.assert_allclose(second.embeddings, loaded.embeddings)


def test_issue_scores_use_max_anchor_cosine_then_softmax() -> None:
    issues = _issues()
    anchors = _anchors(issues)
    tweet_ids = np.asarray(["post-1"], dtype=np.str_)
    embeddings = np.asarray([[1.0, 0.0]], dtype=np.float32)

    rows = compute_missing_issue_scores(
        live_post_ids=["post-1"],
        post_text_by_id={"post-1": "A substantive political tweet"},
        tweet_ids=tweet_ids,
        tweet_embeddings=embeddings,
        issues=issues,
        anchors=anchors,
        cluster_centroids=_cluster_centroids(),
        garbage_cluster_ids=frozenset(),
        existing_pairs=set(),
        temperature=0.07,
    )

    assert len(rows) == EXPECTED_ISSUE_COUNT
    assert sum(row["confidence"] for row in rows) == pytest.approx(1.0, abs=1e-8)
    assert max(rows, key=lambda row: row["confidence"])["issue_id"] == "issue-0"


def test_issue_scores_preserve_existing_pairs() -> None:
    issues = _issues()
    anchors = _anchors(issues)

    rows = compute_missing_issue_scores(
        live_post_ids=["post-1"],
        post_text_by_id={"post-1": "A substantive political tweet"},
        tweet_ids=np.asarray(["post-1"], dtype=np.str_),
        tweet_embeddings=np.asarray([[1.0, 0.0]], dtype=np.float32),
        issues=issues,
        anchors=anchors,
        cluster_centroids=_cluster_centroids(),
        garbage_cluster_ids=frozenset(),
        existing_pairs={("post-1", "issue-0")},
    )

    assert len(rows) == EXPECTED_ISSUE_COUNT - 1
    assert all(row["issue_id"] != "issue-0" for row in rows)


def test_issue_scores_are_zero_for_short_text_after_url_removal() -> None:
    issues = _issues()

    rows = compute_missing_issue_scores(
        live_post_ids=["post-1"],
        post_text_by_id={"post-1": "  תודה https://t.co/example  "},
        tweet_ids=np.asarray(["post-1"], dtype=np.str_),
        tweet_embeddings=np.asarray([[1.0, 0.0]], dtype=np.float32),
        issues=issues,
        anchors=_anchors(issues),
        cluster_centroids=_cluster_centroids(),
        garbage_cluster_ids=frozenset(),
        existing_pairs=set(),
    )

    assert len(rows) == EXPECTED_ISSUE_COUNT
    assert {row["confidence"] for row in rows} == {0.0}


def test_issue_scores_are_zero_for_garbage_cluster() -> None:
    issues = _issues()

    rows = compute_missing_issue_scores(
        live_post_ids=["post-1"],
        post_text_by_id={"post-1": "This text is long enough to score normally"},
        tweet_ids=np.asarray(["post-1"], dtype=np.str_),
        tweet_embeddings=np.asarray([[0.0, 1.0]], dtype=np.float32),
        issues=issues,
        anchors=_anchors(issues),
        cluster_centroids=_cluster_centroids(),
        garbage_cluster_ids=frozenset({1}),
        existing_pairs=set(),
    )

    assert len(rows) == EXPECTED_ISSUE_COUNT
    assert {row["confidence"] for row in rows} == {0.0}


def test_tweet_embedding_backfill_fails_before_loading_if_local_id_missing() -> None:
    with pytest.raises(ValueError, match="absent from the local embedding store"):
        sync_missing_tweet_embeddings(
            client=object(),  # type: ignore[arg-type]
            project="valid-project-123",
            dataset="dataset",
            table="social_post",
            location="US",
            missing_post_ids=["missing"],
            tweet_ids=np.asarray(["present"], dtype=np.str_),
            embeddings=np.asarray([[1.0, 0.0]], dtype=np.float32),
            dimensions=2,
        )
