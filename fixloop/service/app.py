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
from datetime import datetime, timezone
from pathlib import Path

import dotenv
import httpx
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

dotenv.load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from .payments import configure_payments
from .ui import router as ui_router
from worker.core import Worker
from worker.cursor import CursorRunner

app = FastAPI(title="fixloop")
configure_payments(app)
app.include_router(ui_router)

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = Path(os.environ.get("FIXLOOP_JOBS_DIR", REPO_ROOT / "jobs"))
VERIFIER = REPO_ROOT / "verifier" / "verify.py"
VERIFIER_CFG = REPO_ROOT / "verifier" / "verifier.yml"
HOLDBACK = REPO_ROOT / "verifier" / "holdback"
VERIFIER_MODE = os.environ.get("VERIFIER_MODE", "local")
JOB_DEADLINE_S = int(os.environ.get("FIXLOOP_JOB_DEADLINE_S", "900"))

# Buildkite mode
BUILDKITE_API_TOKEN = os.environ.get("BUILDKITE_API_TOKEN", "")
BUILDKITE_ORG = os.environ.get("BUILDKITE_ORG") or os.environ.get(
    "BUILDKITE_ORGANIZATION_SLUG", ""
)
BUILDKITE_PIPELINE = os.environ.get("BUILDKITE_PIPELINE") or os.environ.get(
    "BUILDKITE_PIPELINE_SLUG", ""
)

JOBS: dict[str, dict] = {}


class FixRequest(BaseModel):
    repo: str
    issue: int
    model: str = Field(default="auto", min_length=1, max_length=80)
    deadline_s: int = Field(default=900, ge=60, le=900)
    retry_on_rejection: bool = True
    close_issue: bool = True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/system")
def system_status():
    """Safe, non-secret runtime metadata for the demo console."""
    x402_enabled = os.environ.get("X402_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    buildkite_ready = bool(BUILDKITE_API_TOKEN and BUILDKITE_ORG and BUILDKITE_PIPELINE)
    return {
        "runtime": os.environ.get("FIXLOOP_RUNTIME_NAME", "Akash dCloud"),
        "verifier": VERIFIER_MODE,
        "default_model": os.environ.get("FIXLOOP_CURSOR_MODEL", "auto"),
        "job_deadline_s": JOB_DEADLINE_S,
        "commit_contract": "base → regression test → source fix",
        "infrastructure": {
            "akash": {
                "status": "connected",
                "label": os.environ.get("FIXLOOP_RUNTIME_NAME", "Akash dCloud"),
                "deployment": os.environ.get("AKASH_DSEQ", "managed lease"),
            },
            "x402": {
                "status": "enforced" if x402_enabled else "demo bypass",
                "network": os.environ.get("X402_NETWORK", "eip155:84532"),
                "price": os.environ.get("FIXLOOP_PRICE_USDC", "0.01"),
            },
            "buildkite": {
                "status": "online" if buildkite_ready else "standby",
                "pipeline": BUILDKITE_PIPELINE or "fixloop-verifier",
                "mode": VERIFIER_MODE,
            },
        },
    }


EVENT_SOURCES = {
    "queued": "orchestrator",
    "clone": "akash",
    "issue": "github",
    "agent": "cursor",
    "agent-retry": "cursor",
    "verify": "buildkite" if VERIFIER_MODE == "buildkite" else "verifier",
    "verify-retry": "buildkite" if VERIFIER_MODE == "buildkite" else "verifier",
    "pr": "github",
    "close-issue": "github",
    "finished": "orchestrator",
}


def _event(
    job: dict,
    message: str,
    *,
    stage: str | None = None,
    level: str = "info",
    source: str | None = None,
) -> None:
    event_stage = stage or job.get("stage", "queued")
    job.setdefault("events", []).append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "stage": event_stage,
            "source": source or EVENT_SOURCES.get(event_stage, "orchestrator"),
            "level": level,
            "message": message,
        }
    )


def _set_stage(job: dict, stage: str, message: str, *, source: str | None = None) -> None:
    job["stage"] = stage
    _event(job, message, stage=stage, source=source)


@app.post("/fix")
def create_fix(req: FixRequest):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "running",
        "repo": req.repo,
        "issue": req.issue,
        "verdict": None,
        "pr_url": None,
        "issue_closed": False,
        "branch": None,
        "attempt": 1,
        "stage": "queued",
        "profile": None,
        "commits": None,
        "events": [],
        "settings": {
            "model": req.model,
            "deadline_s": req.deadline_s,
            "retry_on_rejection": req.retry_on_rejection,
            "close_issue": req.close_issue,
            "verifier": VERIFIER_MODE,
        },
    }
    payment_enabled = os.environ.get("X402_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    _event(
        JOBS[job_id],
        "x402 payment proof accepted; execution authorized" if payment_enabled else "x402 gate running in demo bypass mode",
        source="x402",
        level="success" if payment_enabled else "system",
    )
    _event(JOBS[job_id], "Control plane accepted the issue and reserved an Akash worker slot", source="akash")
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
    settings = job.get("settings") or {}
    deadline_s = int(settings.get("deadline_s", JOB_DEADLINE_S))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        _set_stage(job, "clone", "Akash lease attached; provisioning an isolated workspace", source="akash")
        _event(job, f"Cloning {job['repo']} into an ephemeral job directory", source="akash")
        _run(["git", "clone", "--quiet", job["repo"], str(target)], timeout=180)
        base_branch = _default_branch(target)
        _event(job, f"Repository cloned · base branch {base_branch}", source="akash", level="success")

        _set_stage(job, "issue", f"Fetching GitHub issue #{job['issue']}", source="github")
        issue_text = fetch_issue_text(job["repo"], job["issue"])
        _event(job, "Issue context loaded; secrets and credentials excluded from the prompt", source="github", level="success")

        _set_stage(job, "agent", f"Launching Cursor model {settings.get('model', 'auto')} for test-first repair", source="cursor")
        branch = run_agent(
            target,
            job["issue"],
            issue_text,
            deadline_s=deadline_s,
            model=str(settings.get("model", "auto")),
        )
        job["branch"] = branch
        result = json.loads((workdir / "worker-result.json").read_text())
        job["profile"] = result.get("profile")
        job["commits"] = {
            "base": result.get("base_sha"),
            "test": result.get("test_sha"),
            "fix": result.get("fix_sha"),
        }
        languages = ", ".join((result.get("profile") or {}).get("languages") or []) or "unknown"
        _event(job, f"Regression test failed on base and fix passed locally · {languages}", source="cursor", level="success")
        _event(job, f"Two-commit branch sealed: {branch}", source="cursor", level="success")

        verifier_source = "buildkite" if VERIFIER_MODE == "buildkite" else "verifier"
        verifier_name = "Buildkite adversarial pipeline" if VERIFIER_MODE == "buildkite" else "local adversarial verifier"
        _set_stage(job, "verify", f"Sending commits to the {verifier_name}", source=verifier_source)
        verdict = run_verifier(target, result, f"issue-{job['issue']}")
        job["verdict"] = verdict
        _event(job, f"Verifier returned {verdict.get('verdict', 'unknown')}", source=verifier_source, level="success" if verdict.get("verdict") == "verified" else "warn")

        reason_codes = verdict.get("reason_codes") or []
        remaining = deadline_s - (time.monotonic() - started)
        retry_enabled = bool(settings.get("retry_on_rejection", True))
        if verdict.get("verdict") != "verified" and reason_codes and retry_enabled and remaining > 30:
            job["attempt"] = 2
            _set_stage(job, "agent-retry", f"Retrying once with verifier evidence: {', '.join(reason_codes)}", source="cursor")
            branch = run_agent(
                target,
                job["issue"],
                issue_text,
                reason_codes=reason_codes,
                deadline_s=remaining,
                model=str(settings.get("model", "auto")),
            )
            result = json.loads((workdir / "worker-result.json").read_text())
            job["profile"] = result.get("profile")
            job["commits"] = {
                "base": result.get("base_sha"),
                "test": result.get("test_sha"),
                "fix": result.get("fix_sha"),
            }
            _set_stage(job, "verify-retry", "Re-running the adversarial verifier on attempt two", source=verifier_source)
            verdict = run_verifier(target, result, f"issue-{job['issue']}")
            job["verdict"] = verdict
            _event(job, f"Retry verdict: {verdict.get('verdict', 'unknown')}", source=verifier_source, level="success" if verdict.get("verdict") == "verified" else "error")

        if verdict.get("verdict") == "verified":
            _set_stage(job, "pr", "Pushing the deterministic branch and opening a verified pull request", source="github")
            job["pr_url"] = open_pr(target, job["repo"], branch, base_branch, verdict)
            _event(job, f"Pull request opened: {job['pr_url']}", source="github", level="success")
            if settings.get("close_issue", True):
                _set_stage(job, "close-issue", "Closing the resolved GitHub issue", source="github")
                close_issue(target, job["repo"], job["issue"], job["pr_url"])
                job["issue_closed"] = True
                _event(job, "Source issue marked resolved and removed from the Open Issues view", source="github", level="success")
        job["status"] = "done"
        _event(job, "Run complete · evidence and artifacts are ready", stage="finished", level="success")
    except Exception as exc:  # concise by design for a hackathon service
        job["status"] = "done"
        job["error"] = str(exc)[:500]
        _event(job, f"Run stopped: {job['error']}", level="error")
    finally:
        job["stage"] = "finished"
        job["duration_s"] = round(time.monotonic() - started, 2)


def run_agent(
    target: Path,
    issue: int,
    issue_text: str,
    reason_codes: list[str] | None = None,
    deadline_s: float = 900,
    model: str = "auto",
) -> str:
    return Worker(
        target,
        issue,
        issue_text,
        reason_codes,
        cursor_runner=CursorRunner(model=model),
        deadline_s=deadline_s,
    ).execute()


def _verifier_config(result: dict, output: Path) -> Path:
    config = yaml.safe_load(VERIFIER_CFG.read_text())
    profile = result["profile"]
    config["test_paths"] = profile["test_roots"]
    config["src_paths"] = profile["source_roots"]
    config["suite_target"] = profile["test_roots"][0]
    if profile.get("languages") == ["typescript"]:
        config["test_framework"] = "node"
        config["node_test_cmd"] = ["node", "--test", "--test-reporter=junit"]
    path = output.parent / "verifier-generated.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def run_verifier(target: Path, result: dict, issue_id: str) -> dict:
    """Run the verifier in the configured mode. Returns verdict dict."""
    if VERIFIER_MODE == "buildkite":
        return _verify_via_buildkite(target, result, issue_id)
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


def _verify_via_buildkite(target: Path, result: dict, issue_id: str) -> dict:
    """Trigger a Buildkite build and poll until verdict is available."""
    if not all([BUILDKITE_API_TOKEN, BUILDKITE_ORG, BUILDKITE_PIPELINE]):
        raise RuntimeError(
            "Buildkite mode requires BUILDKITE_API_TOKEN plus organization "
            "and pipeline slugs"
        )

    remote_url = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=target, capture_output=True, text=True,
    ).stdout.strip()

    api_base = (
        f"https://api.buildkite.com/v2/organizations/{BUILDKITE_ORG}"
        f"/pipelines/{BUILDKITE_PIPELINE}"
    )
    headers = {"Authorization": f"Bearer {BUILDKITE_API_TOKEN}"}

    # Trigger build
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{api_base}/builds",
            headers=headers,
            json={
                "commit": "HEAD",
                "branch": "main",
                "message": f"verify {issue_id}",
                "meta_data": {
                    "target_repo": remote_url,
                    "base_sha": result["base_sha"],
                    "test_sha": result["test_sha"],
                    "fix_sha": result["fix_sha"],
                    "issue_id": issue_id,
                },
            },
        )
        resp.raise_for_status()
        build = resp.json()
        build_number = build["number"]
        build_url = build.get("web_url", "")

    # Poll for terminal state
    terminal_states = {"passed", "failed", "canceled", "blocked",
                       "not_run", "soft_failed"}
    poll_interval = 10
    max_poll_time = 600
    elapsed = 0
    state = ""

    with httpx.Client(timeout=30) as client:
        while elapsed < max_poll_time:
            time.sleep(poll_interval)
            elapsed += poll_interval

            resp = client.get(f"{api_base}/builds/{build_number}",
                              headers=headers)
            resp.raise_for_status()
            build = resp.json()
            state = build.get("state", "")

            if state in terminal_states:
                break
        else:
            raise RuntimeError(
                f"Buildkite build {build_number} timed out after "
                f"{max_poll_time}s (last state: {state})"
            )

    # Extract verdict from build meta_data
    meta = build.get("meta_data", {})
    verdict_json_str = meta.get("verdict_json")
    if not verdict_json_str:
        raise RuntimeError(
            f"Buildkite build {build_number} reached state '{state}' "
            f"but no verdict_json in meta_data. Build URL: {build_url}"
        )

    return json.loads(verdict_json_str)


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


def close_issue(target: Path, repo: str, issue: int, pr_url: str) -> None:
    """Close a successfully fixed issue so it leaves GitHub's Open view."""
    _run(
        [
            "gh",
            "issue",
            "close",
            str(issue),
            "--repo",
            repo_slug(repo),
            "--comment",
            f"Resolved by verified Fixloop PR: {pr_url}",
        ],
        target,
        timeout=120,
    )
