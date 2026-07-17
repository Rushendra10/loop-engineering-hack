"""fixloop service skeleton -- P1 owns this.

The frozen contracts live HERE as code. Do not change shapes without
telling the whole team; parallel Claude Code sessions depend on them.

Working now: POST /fix, GET /job/{id}, background job runner, verifier in
local mode gating the (stubbed) PR step.
TODO(P1): agent invocation (worker/), buildkite mode, PR opener, SSE.
"""

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="fixloop")

REPO_ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = REPO_ROOT / "jobs"
VERIFIER = REPO_ROOT / "verifier" / "verify.py"
VERIFIER_CFG = REPO_ROOT / "verifier" / "verifier.yml"
HOLDBACK = REPO_ROOT / "verifier" / "holdback"
VERIFIER_MODE = os.environ.get("VERIFIER_MODE", "local")  # local | buildkite

JOBS: dict[str, dict] = {}  # in-memory job table (scope cut: no persistence)


class FixRequest(BaseModel):
    repo: str      # e.g. https://github.com/org/name
    issue: int


@app.post("/fix")
def create_fix(req: FixRequest):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "repo": req.repo, "issue": req.issue,
                    "verdict": None, "pr_url": None, "stage": "queued"}
    threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/job/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404)
    return job


def run_job(job_id: str):
    job = JOBS[job_id]
    workdir = JOBS_DIR / job_id
    target = workdir / "target"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        job["stage"] = "clone"
        subprocess.run(["git", "clone", job["repo"], str(target)],
                       check=True, capture_output=True)

        job["stage"] = "agent"
        branch = run_agent(target, job["issue"])   # TODO(P2 integration)

        job["stage"] = "verify"
        verdict = run_verifier(target, branch, f"issue-{job['issue']}")
        job["verdict"] = verdict

        if verdict["verdict"] == "verified":
            job["stage"] = "pr"
            job["pr_url"] = open_pr(target, branch, verdict)  # TODO(P1)
        job["status"] = "done"
    except Exception as e:  # hackathon-grade error handling, on purpose
        job["status"] = "done"
        job["error"] = str(e)[:500]
    finally:
        job["stage"] = "finished"


def run_agent(target: Path, issue: int) -> str:
    """TODO(P2): invoke the cursor-agent harness in worker/.
    Contract: returns a branch named fixloop/issue-{n} shaped exactly
    base -> test_commit -> fix_commit, present in the target clone."""
    raise NotImplementedError("wire worker/ here")


def run_verifier(target: Path, branch: str, issue_id: str) -> dict:
    if VERIFIER_MODE == "buildkite":
        # TODO(P1): trigger build + poll meta_data.verdict_json.
        # API reference: bottom of verifier/buildkite/pipeline.yml
        raise NotImplementedError("buildkite mode not wired yet")
    out = Path(tempfileish := str(target.parent / "verdict.json"))
    subprocess.run(
        ["python3", str(VERIFIER),
         "--repo", str(target),
         "--base", "main",
         "--test-commit", f"{branch}~1",
         "--fix-commit", branch,
         "--issue", issue_id,
         "--config", str(VERIFIER_CFG),
         "--holdback-root", str(HOLDBACK),
         "--out", str(out)],
        capture_output=True,  # exit code intentionally ignored; verdict JSON is truth
    )
    return json.loads(out.read_text())


def open_pr(target: Path, branch: str, verdict: dict) -> str:
    """TODO(P1): push branch to bot fork + `gh pr create`, PR body =
    verdict summary + 'verified by fixloop'. Needs GH_TOKEN."""
    return "https://github.com/TODO/pull/0"
