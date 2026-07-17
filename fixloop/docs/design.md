# fixloop — paid bug-fixing service on Zero.xyz

**MVP design doc · 3 people · <4 hours · hackathon build**

## One-liner

An agent-callable service listed on Zero.xyz: send us a public GitHub repo +
issue number and USDC per call; a Cursor-SDK agent on Akash attempts the fix
with AkashML inference; our adversarial verifier gates the result; you get a
PR link + verdict JSON. If the verifier doesn't say `verified`, no PR opens.

The verifier is the moat and the demo story: we don't sell "an agent tried,"
we sell "a verified fix or nothing" — including catching the agent gaming its
own tests.

## Sponsor mapping (judging sponsors only)

- **Zero.xyz** — distribution + payments: the service is listed as a Zero
  tool, paid per call in USDC (x402-style settlement).
- **Cursor** — the coding agent: `cursor-agent` CLI/SDK runs the
  test-then-fix loop inside the worker.
- **Akash / AkashML** — compute + inference: API, worker, AND the
  Buildkite agent run as Akash deployments; triage + probe generation (and
  the agent loop if compatible) hit AkashML's OpenAI-compatible endpoint.
- **Buildkite** — the verifier executes as a real Buildkite pipeline
  (trusted bootstrap, dedicated `verifier` queue, verdict published as
  build annotation + meta-data). The agent runs on Akash: decentralized CI
  compute, two sponsors in one component.

No AWS anywhere.

## Architecture (MVP)

```
caller's agent ──(Zero.xyz, USDC/call)──▶ fixloop API (FastAPI, on Akash)
                                              │  POST /fix {repo, issue}
                                              ▼
                                        job runner (same container)
                                              │ clone repo, read issue
                                              ▼
                                  cursor-agent worker (Akash)
                                  inference: AkashML endpoint
                                  output: base → test_commit → fix_commit
                                              │
                                              ▼
                                  verifier (existing block)
                                  VERIFIER_MODE=local     in-process (fallback)
                                  VERIFIER_MODE=buildkite Buildkite build on the
                                                          Akash-hosted agent,
                                                          verdict via meta-data
                                              │
                              verified ───────┼─────── not verified
                                  ▼                        ▼
                        open PR via bot fork        return verdict +
                        (gh CLI, bot token)         reason codes, no PR
                                              │
                                              ▼
                              GET /job/{id} → {status, verdict, pr_url}
```

## Request contract (freeze at T+0:20, do not reopen)

```
POST /fix        {"repo": "https://github.com/org/name", "issue": 42}
             →   {"job_id": "..."}
GET  /job/{id} → {"status": "running|done", "verdict": {...}, "pr_url": "..."}
```

Job dir layout: `/jobs/{id}/target/` (clone), `/jobs/{id}/verdict.json`.
Agent output contract: branch `fixloop/issue-{n}` shaped exactly
`base → test_commit → fix_commit` (verifier rejects anything else with
`NONLINEAR_SUBMISSION` — this is P2's prompt-harness job).

## Scope, recalibrated for 3 people × Claude Code

Everyone runs 2-3 Claude Code sessions in parallel git worktrees. Humans do
the things Claude Code can't compress: booths, deploy approvals, payment
onboarding, demo rehearsal. Contracts-first matters MORE with parallel
agents, not less — freeze the schemas at T+0:20 and treat them as law.

**Core (must ship, same as before):**
- Seeded demo repo end-to-end: paid call → agent → verifier → PR.
- Polling API, dual-mode verifier (`VERIFIER_MODE=local|buildkite`; build
  local first so the demo is never hostage to CI setup, flip to buildkite
  once the pipeline is green), bot-fork PRs, `max_verify_runs = 2`.
- AkashML-generated holdback probes (ship without them if flaky).

**Now in scope (code-shaped, Claude Code makes these cheap):**
- **Any-public-repo profiler**: detect layout/test runner, auto-generate
  `verifier.yml` per repo (src/tests discovery, pytest vs bun/jest via the
  junit seam in runner.py). Demo it live on a repo a judge names.
- **SSE stage streaming**: `/job/{id}/events` streaming loop stages
  (triage → agent attempt N → verifier stage → verdict). Feeds the demo.
- **Dashboard page**: verified-fix rate, gaming attempts caught, cost per
  verified fix (AkashML tokens + Akash spend vs price per call). One
  static page reading the job table.
- **MCP wrapper**: tiny MCP server exposing `fix_github_issue` so any
  Claude Code user calls fixloop as a native tool — second distribution
  surface next to Zero, ~30 min of work, big demo optionality.
- **Proper retry conditioning**: reason-code-specific guidance strings fed
  into attempt 2 (e.g. METAMORPHIC_FAIL → "your fix special-cases the
  repro; fix the general case").

**Still cut (external-dependency-shaped — Claude Code doesn't help):**
- Private repos / caller auth. Webhooks. Pomerium. Multi-region,
  persistence beyond in-memory + job dirs.
- Anything requiring a vendor to do something after T+2:30.

## Known risks + fallbacks (check in the first 20 minutes)

1. **cursor-agent may not accept a custom OpenAI-compatible endpoint
   (AkashML).** Spike this FIRST. Fallback: cursor-agent runs the coding
   loop on Cursor's own models; AkashML handles triage + probe generation.
   Both sponsors still genuinely used — say exactly that to judges.
2. **Zero provider onboarding is not publicly documented.** Go to the Zero
   booth at T+0, ask for provider/listing onboarding. Fallback: stand up a
   raw x402-paid endpoint (xpay-style paywall or Zero's own facilitator if
   they hand us one) and demo "listing pending" with the payment flow live.
3. **Akash deployment friction.** Deploy a hello-world SDL by T+1:00, not
   at T+3:30. Fallback if the worker fights us: API shell on Akash (small,
   reliable) + worker on a laptop behind a cloudflared tunnel; AkashML
   inference keeps the Akash story honest either way.
4. **Agent can't fix the issue live.** Pre-run the demo issue N times;
   demo the pre-warmed job if the live run stalls, with the live one
   racing in a second terminal.

## Team assignments

### P1 — service + verifier + PR path (owns the contracts)
*Verifier block already exists and passes its demo — start from `verifier/README.md`; key facts: submission contract is `base → test_commit → fix_commit` enforced via git parentage, `verify.py` exits 0 iff verified, per-repo policy lives in `verifier/verifier.yml`.*
- FastAPI app: `/fix`, `/job/{id}`, in-memory job table, subprocess runner.
- Buildkite client: trigger build with meta-data, poll for
  `meta_data.verdict_json` (reference snippet at the bottom of
  `verifier/buildkite/pipeline.yml`); behind `VERIFIER_MODE`.
- Wire existing `verifier/verify.py` against the agent's branch; gate on
  verdict; `suspected_overfit` and `rejected` return reason codes, no PR.
- PR opener: bot fork + `gh pr create`, PR body = verdict JSON summary +
  cost + "verified by fixloop" badge text.
- Triage + probe generation via AkashML (one prompt, temperature 0).

### P2 — agent worker (hardest unknown, start with the spike)
- T+0 spike: cursor-agent + AkashML base-URL compatibility (risk #1).
- Prompt harness: issue text in → agent must emit the exact two-commit
  branch shape; enforce with a post-run `git rev-parse` check and one
  self-repair attempt.
- Retry loop: on rejection, feed `reason_codes` back for ONE more attempt
  (budget = 2 verifier runs).
- Containerize the worker (shared base image with P3).

### P3 — Shiva: infra + payments + demo (most parallelizable)
*Starter kit exists (p3-kit): Dockerfile, Akash SDL, demo-repo seed script, runbook with booth questions.*
- T+0: Zero booth — provider onboarding, listing, test wallet (risk #2).
- Dockerfile (python3.12 + git + gh + pytest + cursor-agent), Akash SDL,
  deploy shell by T+1:00, real image by T+3:00.
- Buildkite org + pipeline setup; deploy the agent image
  (`Dockerfile.buildkite-agent`) to Akash; hand P1 the API token.
- Seed + push the public demo repo (port `examples/setup_demo.sh` target,
  file 2 issues: one honest-fixable, one where we show gaming caught).
- Demo script + 90-second pitch doc + backup screen recording.

## Timeline

| clock | milestone | owner |
|---|---|---|
| T+0:00–0:20 | contracts frozen; P2 spike verdict on cursor×AkashML; P3 back from Zero booth with onboarding answer | all |
| T+0:20–1:30 | API + verifier glue working on hand-made branches (local mode); agent produces correct branch shape locally; hello-world + Buildkite agent live on Akash, pipeline runs the demo verdicts | P1 / P2 / P3 |
| T+1:30–2:30 | **integration checkpoint: one end-to-end verified PR on the demo repo, everything on localhost** | P1+P2 |
| T+2:30–3:30 | deploy to Akash; payment path live (Zero listing or raw x402); flip `VERIFIER_MODE=buildkite`; stretch lanes land: profiler, SSE + dashboard, MCP wrapper | P3 / P1 / P2 |
| T+3:30–4:00 | feature freeze; two full demo dry runs; record backup video | all |

Rule for the last 30 minutes: nothing merges except demo fixes. Stretch
features that aren't demo-solid by T+3:30 get feature-flagged off, not
debugged live.

**Parallel-session plan per person** (suggested worktree split):
- P1: session A service+verifier glue · B SSE/events · C MCP wrapper
- P2: session A agent harness · B retry conditioning · C repo profiler
- P3: session A seed/infra scripts · B dashboard · (human: booths, deploys,
  payments, demo capture)

## Demo script (3 minutes)

1. From a stock CLI agent (Claude Code or similar) with Zero installed:
   "there's a bug filed on repo X, find a service to fix it." Agent
   discovers fixloop on Zero, pays per call.
2. Cut to the Akash deployment logs: cursor-agent iterating — then the
   Buildkite build page as the verifier runs on the Akash-hosted agent,
   annotation flipping to the verdict.
3. PR appears on the repo with the verdict JSON in the body. Show the
   USDC charge settled.
4. The kicker: show the second issue where the agent's first attempt
   special-cased the repro — verifier verdict `suspected_overfit`,
   METAMORPHIC_FAIL + literal-scan hit, no PR opened, money not wasted on
   a fake fix. "Every other team demos an agent that claims success. We
   demo the system that checks."

## Judging pitch, one line

Paid, verifiable outcomes for autonomous agents: the loop closes with an
adversarial verifier, so a Zero call buys a *verified* fix — and the
economics (price per verified fix vs inference cost on AkashML) are on the
dashboard.
