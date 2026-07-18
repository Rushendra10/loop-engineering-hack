"""Focused smoke tests for the standalone demo console router."""

import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.ui import router


STATIC_DIR = Path(__file__).resolve().parents[1] / "service" / "static"


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_console_and_assets_are_served() -> None:
    client = make_client()

    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert "The agent proposes" in page.text
    assert "ORCHESTRATION STREAM" in page.text
    assert 'id="model-input"' in page.text
    assert 'id="event-log"' in page.text
    assert "COMMIT CONTRACT" in page.text
    assert 'href="/assets/fixloop.css"' in page.text
    assert 'src="/assets/fixloop.js"' in page.text

    stylesheet = client.get("/assets/fixloop.css")
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert "--verified:" in stylesheet.text

    script = client.get("/assets/fixloop.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["cache-control"] == "no-store"
    assert 'fetch("/fix"' in script.text
    assert "`/job/${encodeURIComponent(jobId)}`" in script.text
    assert 'fetch("/system"' in script.text
    assert "retry_on_rejection" in script.text
    assert "issue_closed" in script.text


def test_missing_asset_is_not_silently_rewritten() -> None:
    client = make_client()
    assert client.get("/assets/missing.js").status_code == 404


def test_javascript_parses_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        return
    result = subprocess.run(
        [node, "--check", str(STATIC_DIR / "fixloop.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
