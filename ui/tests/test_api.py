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



