# P2 Plan — Autonomous Cursor Worker

## Summary

Build the worker that accepts a cloned public Python or TypeScript repository plus an issue, autonomously creates a regression test and fix, and returns:

```text
base → test_commit → fix_commit
```

The worker will attempt arbitrary Python/TypeScript repositories, including monorepos. Verified execution requires a committed lockfile and runnable test infrastructure; unsupported repositories fail clearly rather than producing an unverified branch.

Success means:

- No manual intervention after job submission.
- Branch named `fixloop/issue-{number}`.
- Exactly two commits with test/source separation.
- Python and TypeScript support.
- Multi-workspace fixes supported.
- Dependencies installed using committed lockfiles.
- One verifier-informed retry, with a 15-minute total deadline.

## Architecture and Interfaces

Preserve the frozen service interface:

```python
run(
    target_dir: Path,
    issue_number: int,
    issue_text: str,
    reason_codes: list[str] | None = None,
) -> str  # branch name
```

Add a CLI for isolated testing:

```bash
python -m worker \
  --target /jobs/abc/target \
  --issue-number 42 \
  --issue-file /jobs/abc/issue.txt \
  --reason-codes METAMORPHIC_FAIL
```

Write `<job-dir>/worker-result.json` without modifying the target repository:

```json
{
  "status": "completed",
  "branch": "fixloop/issue-42",
  "base_sha": "...",
  "test_sha": "...",
  "fix_sha": "...",
  "attempt": 1,
  "duration_s": 123,
  "profile": {
    "languages": ["python", "typescript"],
    "workspaces": [],
    "affected_workspaces": [],
    "install_commands": [],
    "test_commands": [],
    "source_roots": [],
    "test_roots": []
  }
}
```

P1 uses this profile to configure the verifier. P1 owns the TypeScript/Jest/Bun verifier adapter and the outer verifier-retry loop.

## Implementation Plan

### 1. Cursor and AkashML spike — maximum 20 minutes

- Install Cursor CLI using its current official installer.
- Authenticate through `CURSOR_API_KEY`.
- Confirm unattended editing with:

```bash
cursor-agent -p --force --output-format stream-json "<prompt>"
```

- Test whether Cursor officially supports routing the coding model through AkashML.
- If unsupported after 20 minutes, use Cursor-hosted models for coding and hand AkashML usage back to P1 for triage and held-back probe generation.
- Do not build a compatibility proxy during the hackathon.

Cursor officially supports headless editing, API-key authentication, structured streaming output, and forced non-interactive execution. [Cursor headless documentation](https://docs.cursor.com/en/cli/headless), [CLI parameters](https://docs.cursor.com/en/cli/reference/parameters).

### 2. Adaptive repository profiler

Implement deterministic profiling before invoking Cursor:

- Detect Python through `pyproject.toml`, `uv.lock`, `poetry.lock`, `Pipfile.lock`, requirements files, pytest configuration, and Python packages.
- Detect TypeScript through `package.json`, `tsconfig.json`, lockfiles, workspace declarations, and test-runner configuration.
- Detect monorepos through npm/pnpm/Yarn workspaces, Nx/Turborepo configuration, uv workspaces, and multiple Python project manifests.
- Infer source roots, test roots, package manager, install commands, test commands, and workspace relationships.
- Use issue text and matching filenames/package names to prioritize affected workspaces.
- Permit coordinated changes across multiple workspaces when the issue requires them.
- Fail with `UNSUPPORTED_REPOSITORY` when no reproducible install or test path can be established.

Locked installs:

- `uv.lock` → `uv sync --frozen`
- `poetry.lock` → `poetry install --no-interaction`
- `Pipfile.lock` → `pipenv sync`
- `package-lock.json` → `npm ci`
- `pnpm-lock.yaml` → `pnpm install --frozen-lockfile`
- Yarn lockfile → immutable/frozen install
- Bun lockfile → `bun install --frozen-lockfile`

### 3. Deterministic two-phase Cursor workflow

The harness—not Cursor—owns Git operations.

Test phase:

1. Record the original base SHA and create `fixloop/issue-{number}`.
2. Prompt Cursor to understand the issue and add only regression tests.
3. Include the repository profile, affected workspaces, and allowed test paths.
4. Explicitly prohibit commits, pushes, source edits, dependency changes, and configuration rewrites.
5. Validate that changes are restricted to discovered test/fixture paths.
6. Run the targeted test and require a genuine failure on the unfixed code.
7. Create `test_commit` deterministically.

Fix phase:

1. Prompt Cursor to implement the smallest root-cause fix.
2. Allow source changes across affected workspaces, but prohibit test changes.
3. Run the targeted test, then affected workspace suites.
4. Require the targeted test to pass twice.
5. Create `fix_commit` deterministically.
6. Verify parentage and per-commit path separation before returning.

If Cursor creates commits itself, normalize the isolated job clone back to the recorded base while retaining its working-tree changes, then let the harness create the required commits.

Allow one extra Cursor repair invocation per attempt when path separation or commit structure is invalid. If repair fails, return a structured worker failure.

### 4. Verifier-conditioned retry

P1 calls the worker again with verifier reason codes. Attempt two rebuilds the branch from the original base and gives Cursor targeted guidance:

- Structural/path violations: rebuild with strict allowed-path instructions.
- Missing, trivial, skipped, or base-passing tests: write a real regression test that fails by assertion.
- Test failure or suite regression: correct the root cause and minimize collateral changes.
- `METAMORPHIC_FAIL` or `LITERAL_OVERFIT`: remove input special-casing and generalize the implementation.
- Flakiness: eliminate timing, randomness, and shared-state dependence.
- Unknown codes: include the exact codes and ask Cursor to inspect the previous attempt critically.

Never exceed two verifier attempts or the 15-minute job deadline.

### 5. Integration requirements

P2 provides P3 with the required worker image dependencies:

- Python 3.12
- Git and GitHub CLI
- Cursor CLI
- Node.js with Corepack
- npm, pnpm, Yarn, and Bun
- uv, Poetry, Pipenv, pytest
- `CURSOR_API_KEY` supplied only at runtime

Secrets must not appear in prompts, logs, commits, or result artifacts. Install and test subprocesses receive a scrubbed environment.

## Test and Demo Plan

Automated checks:

- Profiler fixtures for Python, TypeScript, mixed-language, and monorepo layouts.
- Fake Cursor runner tests for success, timeout, malformed stream output, forbidden edits, self-repair, and no-change results.
- Git-contract tests proving exactly two linear commits and correct path separation.
- Retry tests for structural, regression, and overfitting reason-code categories.
- Deadline enforcement and secret-redaction tests.

Required live demonstrations:

1. Seeded Python issue produces a verified two-commit branch.
2. TypeScript issue produces the same structure and passes the P1 verifier adapter.
3. Monorepo issue changes multiple affected workspaces without touching unrelated packages.
4. Special-cased fix is rejected, retried with `METAMORPHIC_FAIL`, and generalized.
5. Unsupported toolchain returns a clear failure and opens no PR.

## Assumptions and Boundaries

- P2 owns worker profiling, Cursor orchestration, deterministic commits, and retry conditioning.
- P1 owns issue retrieval, verifier execution, TypeScript verifier support, PR creation, and final job state.
- P3 owns container packaging and deployment.
- “Arbitrary repository” means any public Python/TypeScript repository the worker can reproducibly install from committed lockfiles and execute through discoverable test tooling.
- Private repositories, new dependency additions, unsupported native toolchains, production-grade sandboxing, and languages beyond Python/TypeScript are non-blocking future work.
- Once execution mode is enabled, save this plan as `docs/person2-plan.md` before implementing the worker.
