# fixloop worker (P2)

The worker profiles a cloned Python/TypeScript repository, asks Cursor for a
regression test and then a fix, and deterministically creates:

```text
base -> test_commit -> fix_commit
```

Cursor never owns Git operations. If it commits anyway, the harness retains
the file changes and rebuilds the required two-commit history.

## Run

From the `fixloop/` directory:

```bash
cursor-agent login  # one-time browser login; CURSOR_API_KEY is optional
python -m worker \
  --target /jobs/abc/target \
  --issue-number 42 \
  --issue-file /jobs/abc/issue.txt \
  --reason-codes METAMORPHIC_FAIL
```

The Python API remains:

```python
from worker import run

branch = run(target_dir, issue_number, issue_text, reason_codes=None)
```

The result is written to `<target.parent>/worker-result.json`, never into the
target repository. Failures include a machine-readable code such as
`UNSUPPORTED_REPOSITORY`, `TEST_PASSES_ON_BASE`, or `FIX_PATH_VIOLATION`.

## Supported repositories

- Python using pytest and dependency-free projects or `uv.lock`,
  `poetry.lock`, or `Pipfile.lock`.
- TypeScript using a package `test` script and `package-lock.json`,
  `pnpm-lock.yaml`, `yarn.lock`, or a Bun lockfile.
- Mixed projects and common npm/pnpm/Yarn, Nx, Turborepo, and uv-style
  monorepo layouts.

Repositories that declare dependencies without a supported committed
lockfile fail clearly rather than producing an unverified branch.

## Worker image handoff

The runtime image needs Python 3.12, Git, Cursor CLI, Node.js + Corepack,
npm/pnpm/Yarn/Bun, uv/Poetry/Pipenv, and pytest. Install Cursor using its
official command:

```bash
curl https://cursor.com/install -fsS | bash
```

The worker uses Cursor model `auto` by default, which works with a browser
login on the free tier. Set `FIXLOOP_CURSOR_MODEL` to select another model
enabled for the account. `CURSOR_API_KEY` remains an optional non-interactive
authentication path for deployed workers. Install and test commands run with
token/key/password-shaped environment variables removed.

Cursor does not document custom OpenAI-compatible endpoint routing for its
headless agent. Coding therefore uses Cursor-hosted models; AkashML remains
the P1 triage and held-back probe path.

## Tests

```bash
python -m pytest -q worker/tests
```
