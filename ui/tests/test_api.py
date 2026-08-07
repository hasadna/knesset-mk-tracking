from pathlib import Path

from fastapi.testclient import TestClient

from mk_tracking.ui_app.app import ROOT, create_app
from mk_tracking.ui_app.repositories import JsonRepository


def json_app():
    return create_app(JsonRepository(ROOT))


def test_health_and_static_frontend() -> None:
    with TestClient(json_app()) as client:
        assert client.get("/api/health").json() == {
            "status": "ok",
            "backend": "json",
        }
        response = client.get("/")
        assert response.status_code == 200
        assert 'lang="he"' in response.text


def test_mk_list_has_stable_unique_keys_and_filters() -> None:
    with TestClient(json_app()) as client:
        members = client.get("/api/mks").json()
        keys = [member["key"] for member in members]
        assert len(members) == 124
        assert len(keys) == len(set(keys))

        result = client.get("/api/mks", params={"q": "נתניהו"}).json()
        assert [member["key"] for member in result] == ["x-netanyahu"]

        party = members[0]["party"]
        filtered = client.get("/api/mks", params={"party": party}).json()
        assert filtered
        assert all(member["party"] == party for member in filtered)


def test_member_analysis_and_posts_are_scoped() -> None:
    repository = JsonRepository(ROOT)
    with TestClient(create_app(repository)) as client:
        issues_response = client.get("/api/mks/x-netanyahu/issues")
        assert issues_response.status_code == 200
        issues = issues_response.json()
        assert issues["mkKey"] == "x-netanyahu"
        assert len(issues["topics"]) == 15
        assert issues["sourcePosts"]

        posts_response = client.get("/api/mks/x-netanyahu/posts")
        assert posts_response.status_code == 200
        posts = posts_response.json()
        assert posts["total"] == 99
        assert all(post["account"] == "netanyahu" for post in posts["items"])

        sourced_issue = next(topic for topic in issues["topics"] if topic["sources"])
        assert "extendedStance" in sourced_issue
        assert "limitations" in sourced_issue
        assert sourced_issue["postCount"] == len(sourced_issue["sources"])
        assert "limit" not in sourced_issue
        filtered = client.get(
            "/api/mks/x-netanyahu/posts",
            params={"issue": sourced_issue["id"]},
        ).json()
        assert [post["key"] for post in filtered["items"]] == sourced_issue["sources"]


def test_source_post_route_and_input_bounds() -> None:
    repository = JsonRepository(ROOT)
    post_key = repository.posts[0]["key"]
    with TestClient(create_app(repository)) as client:
        response = client.get(f"/api/posts/{post_key}")
        assert response.status_code == 200
        assert response.json()["key"] == post_key

        assert client.get("/api/mks/unknown/issues").status_code == 404
        assert client.get("/api/mks/x-netanyahu/posts", params={"limit": 101}).status_code == 422
        assert client.get("/api/mks/x-netanyahu/posts", params={"issue": "unknown"}).status_code == 422


def test_frontend_never_fetches_ignored_json_files() -> None:
    mk_data_src = Path(ROOT / "src/hooks/useMKData.ts").read_text(encoding="utf-8")
    mk_analysis_src = Path(ROOT / "src/hooks/useMKAnalysis.ts").read_text(encoding="utf-8")
    combined_src = mk_data_src + "\n" + mk_analysis_src

    assert "data/analysis.json" not in combined_src
    assert "data/tweets.normalized.json" not in combined_src
    assert "ZIP" not in combined_src
    assert "CSV" not in combined_src
    assert 'class="limitations"' not in combined_src
    assert "מגבלות:" not in combined_src
    assert "'/api/mks'" in combined_src or '"/api/mks"' in combined_src
    assert "'/api/issues'" in combined_src or '"/api/issues"' in combined_src
    assert "/issues" in combined_src
    assert "/posts?limit=100" not in combined_src


def test_bigquery_repository_preload_summaries() -> None:
    from unittest.mock import MagicMock

    from mk_tracking.ui_app.repositories import BigQueryRepository

    mock_client = MagicMock()
    repo = BigQueryRepository(project="test-proj", dataset="mk_tracking", client=mock_client)

    mock_client.query.return_value.result.return_value = [
        {
            "knesset_member_id": 123,
            "publisher": "בנימין נתניהו",
            "slug": "defense",
            "summary_he": "תומך בביטחון",
            "quality": "strong",
            "rating": 4,
            "topic_post_count": 12,
            "limitations": "",
            "supporting_posts": [
                {
                    "platform_post_id": "1001",
                    "handle": "netanyahu",
                    "posted_at": None,
                    "text": "ציוץ ביטחוני",
                    "url": "https://x.com/netanyahu/status/1001",
                    "engagement": {"like_count": 10},
                }
            ],
        },
        {
            "knesset_member_id": 123,
            "publisher": "בנימין נתניהו",
            "slug": "economy",
            "summary_he": "כלכלה חופשית",
            "quality": "partial",
            "rating": 5,
            "topic_post_count": 3,
            "limitations": "מבוסס על מעט ציוצים",
            "supporting_posts": [],
        },
    ]

    repo.preload_summaries()
    assert mock_client.query.call_count == 1
    assert "WHERE pi.confidence >= 0.2" in mock_client.query.call_args.args[0]
    assert "UNION DISTINCT" in mock_client.query.call_args.args[0]
    assert "mk_issue_summary_supporting_post" in mock_client.query.call_args.args[0]

    mock_client.query.reset_mock()

    issues = repo.get_mk_issues("mk-123")
    assert issues is not None
    assert issues["mkKey"] == "mk-123"
    assert len(issues["topics"]) == 2
    assert issues["topics"][0]["id"] == "defense"
    assert issues["topics"][0]["status"] == "strong"
    assert issues["topics"][0]["postCount"] == 12
    assert issues["topics"][1]["id"] == "economy"
    assert issues["topics"][1]["postCount"] == 3
    assert issues["sourcePosts"][0]["key"] == "x:netanyahu:1001"

    # Query was NOT executed again because summaries were preloaded
    assert mock_client.query.call_count == 0

    assert repo.get_mk_issues("mk-999") is None
    assert mock_client.query.call_count == 0


def test_bigquery_repository_list_mks_in_memory_filter() -> None:
    from unittest.mock import MagicMock

    from mk_tracking.ui_app.repositories import BigQueryRepository

    mock_client = MagicMock()
    repo = BigQueryRepository(project="test-proj", dataset="mk_tracking", client=mock_client)

    mock_client.query.return_value.result.return_value = [
        {
            "knesset_member_id": 123,
            "slug": "netanyahu",
            "full_name_he": "בנימין נתניהו",
            "photo_url": "",
            "bio_he": "",
            "is_current": True,
            "party_name": "הליכוד",
            "party_size": 32,
            "coverage_rows": [{"slug": "defense", "quality": "strong"}],
            "current_roles": ["coalition"],
            "twitter_handle": "netanyahu",
            "twitter_url": "",
            "post_count": 50,
        },
        {
            "knesset_member_id": 456,
            "slug": "lapid",
            "full_name_he": "יאיר לפיד",
            "photo_url": "",
            "bio_he": "",
            "is_current": True,
            "party_name": "יש עתיד",
            "party_size": 24,
            "coverage_rows": [{"slug": "defense", "quality": "partial"}],
            "current_roles": ["opposition"],
            "twitter_handle": "yairlapid",
            "twitter_url": "",
            "post_count": 40,
        },
    ]

    mks = repo.list_mks()
    assert len(mks) == 2
    assert all(member["category"] == "current_mk" for member in mks)
    assert mock_client.query.call_count == 1

    mock_client.query.reset_mock()

    filtered = repo.list_mks(query="נתניהו")
    assert len(filtered) == 1
    assert filtered[0]["key"] == "mk-123"
    assert mock_client.query.call_count == 0

    filtered_party = repo.list_mks(party="יש עתיד")
    assert len(filtered_party) == 1
    assert filtered_party[0]["key"] == "mk-456"
    assert mock_client.query.call_count == 0


def test_security_headers_and_mitigations() -> None:
    with TestClient(json_app()) as client:
        res = client.get("/api/health")
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["server"] == "MK-Tracking"
        assert "Content-Security-Policy" in res.headers

        # Invalid post_key pattern returns 404 immediately
        assert client.get("/api/posts/invalid_key_format").status_code == 404

        # Offset exceeding limit threshold returns 422
        assert client.get("/api/mks/x-netanyahu/posts", params={"offset": 1001}).status_code == 422



