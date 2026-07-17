"""Hackathon API: clone an issue, run the worker, verify it, and open a PR."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from worker.core import Worker


app = FastAPI(title="fixloop")

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = Path(os.environ.get("FIXLOOP_JOBS_DIR", REPO_ROOT / "jobs"))
VERIFIER = REPO_ROOT / "verifier" / "verify.py"
VERIFIER_CFG = REPO_ROOT / "verifier" / "verifier.yml"
HOLDBACK = REPO_ROOT / "verifier" / "holdback"
VERIFIER_MODE = os.environ.get("VERIFIER_MODE", "local")
JOB_DEADLINE_S = int(os.environ.get("FIXLOOP_JOB_DEADLINE_S", "900"))

JOBS: dict[str, dict] = {}


class FixRequest(BaseModel):
    repo: str
    issue: int


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/fix")
def create_fix(req: FixRequest):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "running",
        "repo": req.repo,
        "issue": req.issue,
        "verdict": None,
        "pr_url": None,
        "branch": None,
        "attempt": 1,
        "stage": "queued",
    }
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/job/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404)
    return job


def _run(command: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(f"{command[0]} failed: {(detail[-1] if detail else 'unknown error')[:300]}")
    return proc.stdout.strip()


def repo_slug(repo: str) -> str:
    match = re.fullmatch(
        r"(?:https?://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?",
        repo,
    )
    if not match:
        raise RuntimeError("only github.com repository URLs are supported")
    return match.group(1)


def fetch_issue_text(repo: str, issue: int) -> str:
    slug = repo_slug(repo)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "fixloop-worker"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/repos/{slug}/issues/{issue}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            issue_data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub issue lookup failed with HTTP {exc.code}") from exc
    title = str(issue_data.get("title") or "").strip()
    body = str(issue_data.get("body") or "").strip()
    return f"{title}\n\n{body}".strip()


def _default_branch(target: Path) -> str:
    branch = _run(["git", "branch", "--show-current"], target)
    if branch:
        return branch
    remote_head = _run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], target)
    return remote_head.removeprefix("origin/")


def run_job(job_id: str):
    job = JOBS[job_id]
    workdir = JOBS_DIR / job_id
    target = workdir / "target"
    started = time.monotonic()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        job["stage"] = "clone"
        _run(["git", "clone", "--quiet", job["repo"], str(target)], timeout=180)
        base_branch = _default_branch(target)

        job["stage"] = "issue"
        issue_text = fetch_issue_text(job["repo"], job["issue"])

        job["stage"] = "agent"
        branch = run_agent(target, job["issue"], issue_text, deadline_s=JOB_DEADLINE_S)
        job["branch"] = branch
        result = json.loads((workdir / "worker-result.json").read_text())

        job["stage"] = "verify"
        verdict = run_verifier(target, result, f"issue-{job['issue']}")
        job["verdict"] = verdict

        reason_codes = verdict.get("reason_codes") or []
        remaining = JOB_DEADLINE_S - (time.monotonic() - started)
        if verdict.get("verdict") != "verified" and reason_codes and remaining > 30:
            job["attempt"] = 2
            job["stage"] = "agent-retry"
            branch = run_agent(
                target,
                job["issue"],
                issue_text,
                reason_codes=reason_codes,
                deadline_s=remaining,
            )
            result = json.loads((workdir / "worker-result.json").read_text())
            job["stage"] = "verify-retry"
            verdict = run_verifier(target, result, f"issue-{job['issue']}")
            job["verdict"] = verdict

        if verdict.get("verdict") == "verified":
            job["stage"] = "pr"
            job["pr_url"] = open_pr(target, job["repo"], branch, base_branch, verdict)
        job["status"] = "done"
    except Exception as exc:  # concise by design for a hackathon service
        job["status"] = "done"
        job["error"] = str(exc)[:500]
    finally:
        job["stage"] = "finished"
        job["duration_s"] = round(time.monotonic() - started, 2)


def run_agent(
    target: Path,
    issue: int,
    issue_text: str,
    reason_codes: list[str] | None = None,
    deadline_s: float = 900,
) -> str:
    return Worker(target, issue, issue_text, reason_codes, deadline_s=deadline_s).execute()


def _verifier_config(result: dict, output: Path) -> Path:
    config = yaml.safe_load(VERIFIER_CFG.read_text())
    profile = result["profile"]
    config["test_paths"] = profile["test_roots"]
    config["src_paths"] = profile["source_roots"]
    config["suite_target"] = profile["test_roots"][0]
    path = output.parent / "verifier-generated.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def run_verifier(target: Path, result: dict, issue_id: str) -> dict:
    if VERIFIER_MODE == "buildkite":
        raise NotImplementedError("buildkite mode is not wired")
    output = target.parent / "verdict.json"
    config = _verifier_config(result, output)
    subprocess.run(
        [
            "python3",
            str(VERIFIER),
            "--repo",
            str(target),
            "--base",
            result["base_sha"],
            "--test-commit",
            result["test_sha"],
            "--fix-commit",
            result["fix_sha"],
            "--issue",
            issue_id,
            "--config",
            str(config),
            "--holdback-root",
            str(HOLDBACK),
            "--out",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=JOB_DEADLINE_S,
    )
    if not output.exists():
        raise RuntimeError("verifier failed without producing a verdict")
    return json.loads(output.read_text())


def open_pr(target: Path, repo: str, branch: str, base_branch: str, verdict: dict) -> str:
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        raise RuntimeError("GH_TOKEN is required to push and open a PR")

    slug = repo_slug(repo)
    login = _run(["gh", "api", "user", "--jq", ".login"], target)
    _run(["gh", "auth", "setup-git"], target)
    pushed_to_fork = False
    push = subprocess.run(
        ["git", "push", "--force", "origin", f"{branch}:{branch}"],
        cwd=target,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if push.returncode:
        fork_slug = f"{login}/{slug.split('/', 1)[1]}"
        exists = subprocess.run(
            ["gh", "api", f"repos/{fork_slug}"], cwd=target, capture_output=True, text=True
        ).returncode == 0
        if not exists:
            _run(["gh", "repo", "fork", slug, "--clone=false"], target, timeout=120)
        subprocess.run(["git", "remote", "remove", "fixloop-fork"], cwd=target, capture_output=True)
        _run(["git", "remote", "add", "fixloop-fork", f"https://github.com/{fork_slug}.git"], target)
        _run(["git", "push", "--force", "fixloop-fork", f"{branch}:{branch}"], target, timeout=120)
        pushed_to_fork = True

    summary = ", ".join(verdict.get("reason_codes") or []) or "all verifier stages passed"
    head = f"{login}:{branch}" if pushed_to_fork else branch
    return _run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            slug,
            "--base",
            base_branch,
            "--head",
            head,
            "--title",
            f"Fix issue #{verdict.get('issue', 'unknown')}",
            "--body",
            f"Automated two-commit fix generated by Fixloop.\n\nVerifier: {summary}.\n\nVerified by Fixloop.",
        ],
        target,
        timeout=120,
    ).splitlines()[-1]
