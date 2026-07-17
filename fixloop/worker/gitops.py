"""Git operations owned by the harness, never by Cursor."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import WorkerError


GENERATED_PARTS = {".venv", "venv", "node_modules", ".pytest_cache", "__pycache__", "coverage", "dist", "build"}


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    if check and proc.returncode:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown Git error"
        raise WorkerError("GIT_FAILED", f"git {' '.join(args)}: {detail}")
    return proc.stdout.strip()


def resolve_base(repo: Path, branch: str) -> str:
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo
    ).returncode == 0
    return git(repo, "rev-parse", f"{branch}~2" if exists else "HEAD")


def prepare_branch(repo: Path, branch: str, base: str) -> None:
    git(repo, "checkout", "-B", branch, base)
    git(repo, "reset", "--hard", base)
    git(repo, "clean", "-fd", "-e", "worker-result.json")


def changed_paths(repo: Path) -> list[str]:
    paths: set[str] = set()
    for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only"), ("ls-files", "--others", "--exclude-standard")):
        paths.update(line for line in git(repo, *args).splitlines() if line)
    return sorted(path for path in paths if not GENERATED_PARTS.intersection(Path(path).parts))


def normalize_cursor_commits(repo: Path, expected_head: str) -> None:
    if git(repo, "rev-parse", "HEAD") != expected_head:
        # Keep all file changes but discard any commit structure Cursor created.
        git(repo, "reset", "--soft", expected_head)
        git(repo, "reset")


def commit_paths(repo: Path, paths: list[str], message: str) -> str:
    if not paths:
        raise WorkerError("NO_CHANGES", f"nothing to commit for {message}")
    git(repo, "add", "--", *paths)
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "fixloop worker",
        "GIT_AUTHOR_EMAIL": "worker@fixloop.local",
        "GIT_COMMITTER_NAME": "fixloop worker",
        "GIT_COMMITTER_EMAIL": "worker@fixloop.local",
    })
    proc = subprocess.run(["git", "commit", "-m", message], cwd=repo, env=env, text=True, capture_output=True)
    if proc.returncode:
        raise WorkerError("GIT_FAILED", proc.stderr.strip() or "commit failed")
    return git(repo, "rev-parse", "HEAD")


def commit_files(repo: Path, older: str, newer: str) -> list[str]:
    return sorted(line for line in git(repo, "diff", "--name-only", f"{older}..{newer}").splitlines() if line)


def verify_contract(repo: Path, base: str, test_sha: str, fix_sha: str, test_paths: list[str], source_paths: list[str]) -> None:
    if git(repo, "rev-parse", f"{test_sha}^") != base or git(repo, "rev-parse", f"{fix_sha}^") != test_sha:
        raise WorkerError("NONLINEAR_SUBMISSION", "expected base -> test_commit -> fix_commit")
    tests = commit_files(repo, base, test_sha)
    fixes = commit_files(repo, test_sha, fix_sha)
    if not tests or any(not path_in_roots(path, test_paths) for path in tests):
        raise WorkerError("TEST_COMMIT_PATH_VIOLATION", ", ".join(tests) or "test commit is empty")
    if not fixes or any(not path_in_roots(path, source_paths) for path in fixes):
        raise WorkerError("FIX_COMMIT_PATH_VIOLATION", ", ".join(fixes) or "fix commit is empty")


def path_in_roots(path: str, roots: list[str]) -> bool:
    return any(root == "." or path == root.rstrip("/") or path.startswith(root.rstrip("/") + "/") for root in roots)
