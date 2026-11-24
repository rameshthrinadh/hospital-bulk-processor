"""Serving the built web UI from the API (FRONTEND_DIR)."""

import dataclasses

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import create_app
from tests.fake_directory import FakeDirectory


def client(**overrides):
    cfg = dataclasses.replace(settings, directory_api_url="http://directory.test", **overrides)
    return TestClient(create_app(cfg, transport=httpx.MockTransport(FakeDirectory()._handle)))


@pytest.fixture
def ui(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>UI</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside")
    with client(frontend_dir=str(dist)) as c:
        yield c


def test_root_and_client_routes_serve_index(ui):
    for path in ("/", "/history", "/batches/00000000-0000-4000-8000-000000000000"):
        response = ui.get(path)
        assert response.status_code == 200
        assert "<title>UI</title>" in response.text


def test_static_files_served_and_assets_cached(ui):
    asset = ui.get("/assets/app-abc123.js")
    assert asset.text == "console.log(1)"
    assert "immutable" in asset.headers["cache-control"]
    assert ui.get("/favicon.svg").text == "<svg/>"


def test_api_routes_win_and_unknown_api_paths_stay_json_404(ui):
    assert ui.get("/health").json() == {"status": "ok"}
    assert ui.get("/docs").status_code == 200
    unknown = ui.get("/hospitals/bulk/00000000-0000-4000-8000-000000000000/nope")
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Not Found"}


def test_files_outside_the_ui_directory_are_never_served(ui):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "outside" not in ui.get(path).text


def test_without_frontend_root_redirects_to_docs():
    with client(frontend_dir="") as c:
        response = c.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_missing_index_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="index.html"):
        client(frontend_dir=str(tmp_path))
