from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def command(repo: Path, *args: str) -> str:
    proc = subprocess.run(args, cwd=repo, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path):
    def make(files: dict[str, str]) -> Path:
        repo = tmp_path / "job" / "target"
        repo.mkdir(parents=True)
        command(repo, "git", "init", "-q", "-b", "main")
        command(repo, "git", "config", "user.name", "test")
        command(repo, "git", "config", "user.email", "test@example.com")
        for name, content in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        command(repo, "git", "add", "-A")
        command(repo, "git", "commit", "-qm", "base")
        return repo

    return make
