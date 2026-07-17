from pathlib import Path

import pytest

from worker.cursor import CursorRunner
from worker.errors import WorkerError


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def test_cursor_runner_accepts_browser_auth_without_api_key(tmp_path, monkeypatch):
    binary = executable(tmp_path / "cursor-agent", "printf '%s\\n' '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false}'\n")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    CursorRunner(str(binary))(tmp_path, "prompt", 5, "test")


def test_cursor_runner_rejects_malformed_stream(tmp_path, monkeypatch):
    binary = executable(tmp_path / "cursor-agent", "echo not-json\n")
    monkeypatch.setenv("CURSOR_API_KEY", "not-a-real-secret")
    with pytest.raises(WorkerError, match="CURSOR_MALFORMED_OUTPUT"):
        CursorRunner(str(binary))(tmp_path, "prompt", 5, "test")


def test_cursor_runner_explains_missing_browser_auth(tmp_path, monkeypatch):
    binary = executable(tmp_path / "cursor-agent", "echo 'Not authenticated' >&2\nexit 1\n")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(WorkerError, match="CURSOR_AUTH_MISSING"):
        CursorRunner(str(binary))(tmp_path, "prompt", 5, "test")
