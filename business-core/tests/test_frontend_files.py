"""FastAPI serves the built web app in the container: files, page links, and API paths left alone."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.frontend_files import mount_frontend


@pytest.fixture
def site(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("nope")
    app = FastAPI()

    @app.get("/api/v1/thing")
    def thing():
        return {"ok": True}

    mount_frontend(app, tmp_path)
    return TestClient(app)


def test_page_links_get_the_app(site) -> None:
    for path in ("/", "/agent", "/receiving?po=PO-123"):
        response = site.get(path)
        assert response.status_code == 200 and "root" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_hashed_files_are_cached_and_root_files_served(site) -> None:
    script = site.get("/assets/index-abc123.js")
    assert script.status_code == 200 and "immutable" in script.headers["cache-control"]
    assert site.get("/favicon.svg").text == "<svg/>"


def test_api_paths_are_not_the_app(site) -> None:
    assert site.get("/api/v1/thing").json() == {"ok": True}
    assert site.get("/api/v1/missing").status_code == 404
    assert site.get("/api").status_code == 404


def test_no_files_outside_the_build(site) -> None:
    assert "nope" not in site.get("/../secret.txt").text
    assert "nope" not in site.get("/%2e%2e/secret.txt").text


def test_a_missing_build_is_a_clear_error(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="index.html"):
        mount_frontend(FastAPI(), tmp_path)
