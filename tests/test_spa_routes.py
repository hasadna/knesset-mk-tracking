"""A deep link into a client-side route must serve the app, not a 404."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mk_tracking.ui_app import app as app_module

INDEX = "<!doctype html><title>mk-tracking</title>"


def test_deep_link_serves_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A stand-in for ui/dist, so this runs without `npm run build`.
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text(INDEX)
    monkeypatch.setattr(app_module, "UI_ROOT", tmp_path)

    # The repository is irrelevant here: this is about routing, and the
    # lifespan pre-warm tolerates a backend it cannot query.
    with TestClient(app_module.create_app(repository=object())) as client:  # type: ignore[arg-type]
        assert client.get("/mk/mk-30695").text == INDEX
        assert client.get("/api/nope").status_code == 404
