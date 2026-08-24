"""The vote ingestion deletes before it writes; these pin down when it must not.

`delete_orphan_mk_votes()` removes every mk_vote row whose vote_event was not in
the current fetch. That is correct after a successful fetch and catastrophic
after a failed one — an empty result would delete the entire vote record and
commit. The whole table is ~900,000 rows that take ~50 minutes to rebuild, so
the guards below are load-bearing rather than defensive decoration.
"""

from __future__ import annotations

import urllib.error

import pytest

from mk_tracking.download_knesset_data import over_api, upload_over_to_postgres


def _explode(*args, **kwargs):
    raise AssertionError("connected to the database despite an empty fetch")


@pytest.fixture
def never_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any database connection during these tests is itself the failure."""
    monkeypatch.setattr(upload_over_to_postgres.psycopg, "connect", _explode)


def _run_with_fetches(monkeypatch, events, results):
    monkeypatch.setattr(upload_over_to_postgres, "fetch_over_vote_events",
                        lambda **kwargs: events)
    monkeypatch.setattr(upload_over_to_postgres, "fetch_over_vote_results",
                        lambda *args, **kwargs: results)
    monkeypatch.setattr(upload_over_to_postgres, "load_crosswalk", lambda path: {})
    monkeypatch.setattr(
        "sys.argv",
        ["upload_over_to_postgres", "--database-url", "postgresql://unused/db"],
    )
    return upload_over_to_postgres.main()


def test_empty_event_fetch_refuses_to_touch_the_database(
    monkeypatch: pytest.MonkeyPatch, never_connects: None
) -> None:
    """An outage must not be read as 'every vote event has been deleted upstream'."""
    assert _run_with_fetches(monkeypatch, events=[], results=[]) == 1


def test_empty_result_fetch_refuses_too(
    monkeypatch: pytest.MonkeyPatch, never_connects: None
) -> None:
    """Events but no results would delete every row and re-insert nothing."""
    events = [{"external_key": "k", "knesset_bill_id": None}]
    assert _run_with_fetches(monkeypatch, events=events, results=[]) == 1


class _Failure(urllib.error.HTTPError):
    def __init__(self, code: int) -> None:
        super().__init__("https://over.example", code, "boom", {}, None)


def test_api_failure_raises_instead_of_returning_no_rows(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed request must be distinguishable from a page with no rows.

    Callers page until a request comes back empty, so returning `{"rows": []}`
    on failure would silently truncate the fetch — or, on a total outage, return
    a complete-looking empty result.
    """
    monkeypatch.setattr(over_api.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        over_api.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(_Failure(500)),
    )
    with pytest.raises(over_api.OverApiError):
        over_api.run_over_sql("SELECT 1", retries=2)


def test_rate_limit_exhaustion_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sustained 429 is the realistic outage; it must not look like no data."""
    monkeypatch.setattr(over_api.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        over_api.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(_Failure(429)),
    )
    with pytest.raises(over_api.OverApiError):
        over_api.run_over_sql("SELECT 1 OFFSET 5000", retries=2)


def test_pagination_still_ends_on_a_genuinely_empty_page(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success-with-no-rows path must keep working, or paging never stops."""
    pages = [{"columns": ["a"], "rows": [{"a": 1}]}, {"columns": ["a"], "rows": []}]
    monkeypatch.setattr(over_api, "run_over_sql", lambda sql, **k: pages.pop(0))
    monkeypatch.setattr(over_api.time, "sleep", lambda _seconds: None)
    assert over_api.fetch_all_over_sql("SELECT a", page_size=1) == [{"a": 1}]
