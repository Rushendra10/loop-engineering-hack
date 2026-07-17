# fixloop

Paid bug-fixing service for AI agents, built at the tokens& loop engineering
hackathon. An agent (or human) pays per call via Zero.xyz; a Cursor-SDK
agent on Akash attempts the fix with AkashML inference; an adversarial
verifier running on Buildkite (agent hosted on Akash) gates the result.
**Verified fix or nothing** — no PR opens unless the verifier says so.

Full design + timeline + assignments: `docs/design.md`.

## Layout

    docs/       design doc (read this first)
    verifier/   DONE — adversarial verifier + Buildkite pipeline (P-verifier)
    service/    FastAPI API + verifier glue + PR path (P1) — skeleton in place
    worker/     cursor-agent harness (P2) — contract in worker/README.md
    infra/      Dockerfiles, Akash SDL, demo repo seeder, P3 runbook (P3)
    jobs/       runtime job dirs (gitignored)

## Verify the verifier works (30 seconds)

    pip install pytest pyyaml
    bash verifier/examples/setup_demo.sh /tmp/demo-target
    bash verifier/examples/run_demo.sh   /tmp/demo-target

Expected: good-fix → verified · gamed-fix → suspected_overfit · lazy-fix → rejected

## Run the service skeleton

    pip install fastapi uvicorn
    uvicorn service.app:app --port 8080

Frozen contracts (do not change without telling everyone):
`POST /fix {repo, issue}` → `{job_id}` · `GET /job/{id}` → status/verdict/pr_url
· agent branch shape `base → test_commit → fix_commit`.
