"""A deep link into a client-side route must serve the app, not a 404."""

from fastapi.testclient import TestClient

from mk_tracking.ui_app.app import create_app


def test_deep_link_serves_index() -> None:
    # The repository is irrelevant here: this is about routing, and the
    # lifespan pre-warm tolerates a backend it cannot query.
    client = TestClient(create_app(repository=object()))  # type: ignore[arg-type]
    with client:
        deep = client.get("/mk/mk-30695")
        assert deep.status_code == 200
        assert deep.text == client.get("/").text
        assert client.get("/api/nope").status_code == 404
