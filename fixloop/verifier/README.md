# robobun-verifier

Adversarial verification for agent-submitted bug fixes. Built for a
loop-engineering system in the robobun pattern: an agent reads a bug report,
writes a regression test, attempts a fix, and this verifier decides — the
agent's opinion of its own work is never state.

**Design principle: the verifier is adversarial to the agent, not cooperative
with it.** Assume the agent will accidentally reward-hack, because it will.

## The submission contract

```
base ──▶ test_commit ──▶ fix_commit      (linear, exactly this shape)
```

- `test_commit` touches only `test_paths` — the regression test.
- `fix_commit` touches only `src_paths` — the fix.

Enforced structurally via git parentage + per-commit diffs. This single rule
kills the largest gaming class (a fix that quietly weakens the test meant to
verify it) before anything executes.

## Pipeline stages

| stage | check | catches |
|---|---|---|
| 1. diff guards | commit separation, protected paths, evasion patterns (`skip`/`xfail`/`assert True`/swallowed exceptions/retry wrappers), assertion-count monotonicity | test deletion, self-modifying CI, trivial tests |
| 2. fail-on-base | new test must **fail its assertion** at `test_commit` (distinguishes assertion failure from harness crash via junit `<failure>` vs `<error>`) | tests that don't capture the bug, syntax-error tests |
| 3. pass-on-fix | new test passes at `fix_commit`, **twice back-to-back** | fixes that don't work, flaky regression tests |
| 4. suite compare | full suite on base + fix worktrees, junit set diff; reports *regressions introduced*, not pre-existing reds; quarantine list gets one retry, all else deterministic | collateral damage |
| 5a. metamorphic probes | held-back perturbed repro variants, generated at triage, **never shown to the agent** | fixes that special-case the repro input |
| 5b. literal scan | fix-diff added lines containing literals lifted from the new test (advisory by default — highest false-positive risk; `literal_scan_blocking: true` to gate) | same, statically |
| 6. verdict | one JSON blob: `verified` / `rejected` / `suspected_overfit` + reason codes | — |

Reason codes matter more than the boolean: they are what the agent conditions
on next attempt. Specific codes make iteration 2 better than iteration 1
instead of a coin reflip.

## Quick start

```bash
pip install pytest pyyaml
bash verifier/examples/setup_demo.sh /tmp/demo-target
bash verifier/examples/run_demo.sh   /tmp/demo-target
```

The demo target has a seeded off-by-one bug and three agent submissions:

```
good-fix   honest test + root-cause fix       -> verified
gamed-fix  special-cases the repro input      -> suspected_overfit (METAMORPHIC_FAIL)
lazy-fix   `assert True` instead of a test    -> rejected (TRIVIAL_ASSERTION)
```

## Running against your repo

```bash
python3 verifier/verify.py \
  --repo /path/to/target \
  --base main \
  --test-commit agent-branch~1 \
  --fix-commit  agent-branch \
  --issue issue-123 \
  --config verifier/verifier.yml \
  --holdback-root holdback \
  --out verdict.json
```

Exit code is 0 iff `verified`, so CI step status mirrors the verdict.
Edit `verifier/verifier.yml` for your repo's `test_paths` / `src_paths` /
`protected_paths` / test commands.

## Buildkite integration

`verifier/buildkite/pipeline.yml` runs the verifier in Docker on a dedicated
`verifier` agent queue and publishes the verdict as an artifact + annotation
+ build meta-data for the orchestrator to poll.

**Trust boundary — do not skip this part:**

- The pipeline definition lives in THIS repo, uploaded by
  `verifier/buildkite/scripts/bootstrap.sh` from a trusted checkout. Never
  `pipeline upload` from the agent's branch — that lets the agent rewrite
  its own verifier.
- Three credentials, three blast radii: the coding agent can push branches
  and open PRs only; the verifier agent holds a read-only clone token; only
  the orchestrator can trigger builds and merge.
- The coding sandbox's network policy (e.g. Pomerium) should allow
  read-only Buildkite status and block all mutation endpoints.
- `conftest.py` is a protected path because it controls pytest's import
  machinery; CI config is protected because it *is* the verifier.

## Loop integration notes

- Verifier runs are the expensive resource: enforce a verify budget
  (e.g. 3 per issue) in the orchestrator, separate from token budgets.
  Prompt the agent to run tests locally first — local runs are hints,
  verifier runs are truth.
- The verdict JSON is the loop's ONLY ground truth. Agent transcripts and
  LLM-judge opinions are context, never state transitions.
- Holdback probes live in `holdback/<issue-id>/test_*.py`, written at triage
  time by perturbing the repro (different values, orderings,
  boundary-adjacent cases).

## Repo layout

```
verify.py           CLI + stage orchestration
guards.py           static diff guards + literal scan
runner.py           pytest execution, junit parsing, suite compare, probes
verifier.yml        per-target policy (lives with the verifier, never the target)
buildkite/          trusted pipeline + bootstrap
holdback/<issue>/   held-back metamorphic probes
examples/           end-to-end demo (setup + run)
```
(all paths relative to `verifier/` in the loop-eng-building-blocks monorepo)

## Known limitations (hackathon honesty)

- Evasion patterns and assertion counting are regex-level, tuned for
  pytest/JS idioms; AST-level checks would be the production version.
- Literal scan is naive substring matching — that's why it defaults to
  advisory.
- Suite compare assumes the suite fits in the timeout; large repos need
  affected-test selection.
- Python/pytest is the only wired-up runner; `pytest_cmd`/junit parsing is
  the seam to swap in `bun test --reporter=junit`, jest, etc.
