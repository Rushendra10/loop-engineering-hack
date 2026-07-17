from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from worker.core import Worker, _scrubbed_env, retry_guidance
from worker.errors import WorkerError


BASE_FILES = {
    "src/calc.py": "def add(a, b):\n    return a + b + 1\n",
    "tests/test_existing.py": "from src.calc import add\n\ndef test_exported():\n    assert callable(add)\n",
}


class FakeCursor:
    def __init__(self, commit_test: bool = False, forbidden_first: bool = False):
        self.phases: list[str] = []
        self.commit_test = commit_test
        self.forbidden_first = forbidden_first

    def __call__(self, repo: Path, prompt: str, timeout: float, phase: str):
        self.phases.append(phase)
        if phase == "test":
            (repo / "tests/test_regression.py").write_text(
                "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
            )
            if self.forbidden_first:
                (repo / "src/calc.py").write_text("def add(a, b):\n    return 999\n")
            if self.commit_test:
                subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
                subprocess.run(["git", "commit", "-qm", "cursor should not own this"], cwd=repo, check=True)
        elif phase == "test-repair":
            (repo / "src/calc.py").write_text(BASE_FILES["src/calc.py"])
        elif phase == "fix":
            (repo / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")


def test_worker_creates_exact_two_commit_branch_and_result(git_repo):
    repo = git_repo(BASE_FILES)
    fake = FakeCursor(commit_test=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    branch = Worker(repo, 42, "add returns one too many", cursor_runner=fake).execute()

    assert branch == "fixloop/issue-42"
    history = subprocess.check_output(["git", "rev-list", "--first-parent", "HEAD"], cwd=repo, text=True).splitlines()
    assert len(history) == 3
    assert history[-1] == base
    assert subprocess.check_output(["git", "diff", "--name-only", "HEAD~2..HEAD~1"], cwd=repo, text=True).splitlines() == ["tests/test_regression.py"]
    assert subprocess.check_output(["git", "diff", "--name-only", "HEAD~1..HEAD"], cwd=repo, text=True).splitlines() == ["src/calc.py"]
    result = json.loads((repo.parent / "worker-result.json").read_text())
    assert result["status"] == "completed"
    assert result["base_sha"] == base
    assert result["attempt"] == 1


def test_worker_uses_one_path_repair(git_repo):
    repo = git_repo(BASE_FILES)
    fake = FakeCursor(forbidden_first=True)
    Worker(repo, 7, "add is wrong", cursor_runner=fake).execute()
    assert fake.phases == ["test", "test-repair", "fix"]


def test_worker_writes_structured_failure_for_no_changes(git_repo):
    repo = git_repo(BASE_FILES)

    def noop(repo, prompt, timeout, phase):
        return None

    with pytest.raises(WorkerError, match="TEST_PATH_VIOLATION"):
        Worker(repo, 1, "bug", cursor_runner=noop).execute()
    result = json.loads((repo.parent / "worker-result.json").read_text())
    assert result["status"] == "failed"
    assert result["error"]["code"] == "TEST_PATH_VIOLATION"


def test_retry_guidance_covers_overfit_and_unknown_codes():
    guidance = retry_guidance(["METAMORPHIC_FAIL", "SOMETHING_NEW"])
    assert "general behavior" in guidance
    assert "SOMETHING_NEW" in guidance


def test_attempt_two_rebuilds_from_original_base(git_repo):
    repo = git_repo(BASE_FILES)
    Worker(repo, 9, "add bug", cursor_runner=FakeCursor()).execute()
    first_base = subprocess.check_output(["git", "rev-parse", "HEAD~2"], cwd=repo, text=True).strip()
    Worker(repo, 9, "add bug", ["METAMORPHIC_FAIL"], cursor_runner=FakeCursor()).execute()
    assert subprocess.check_output(["git", "rev-parse", "HEAD~2"], cwd=repo, text=True).strip() == first_base
    assert json.loads((repo.parent / "worker-result.json").read_text())["attempt"] == 2


def test_deadline_and_secret_scrubbing(git_repo, monkeypatch):
    repo = git_repo(BASE_FILES)
    monkeypatch.setenv("AKASHML_API_KEY", "never-leak")
    monkeypatch.setenv("SAFE_SETTING", "keep")
    assert "AKASHML_API_KEY" not in _scrubbed_env()
    assert _scrubbed_env()["SAFE_SETTING"] == "keep"
    with pytest.raises(WorkerError, match="DEADLINE_EXCEEDED"):
        Worker(repo, 2, "bug", cursor_runner=FakeCursor(), deadline_s=0).execute()
