# P2 worker handoff

## Interface

P1 should call:

```python
from worker import run

branch = run(
    target_dir=target,
    issue_number=issue_number,
    issue_text=issue_title_and_body,
    reason_codes=previous_verdict_reason_codes or None,
)
```

The target must be a clean public-repository clone. The call returns
`fixloop/issue-{number}` and writes `worker-result.json` beside (not inside)
the target clone. A completed branch is always shaped:

```text
base -> regression-test commit -> source-fix commit
```

P1 remains responsible for issue retrieval, verifier invocation, the maximum
two-attempt outer loop, and PR creation. On a rejected first attempt, call the
worker again with the verifier's `reason_codes`; it rebuilds from the original
base and reports `attempt: 2`.

## Cursor authentication

The worker defaults to `FIXLOOP_CURSOR_MODEL=auto`.

- Local demo: run `cursor-agent login` once. No `CURSOR_API_KEY` is needed.
- Container/Akash: provide `CURSOR_API_KEY`; browser credentials are not baked
  into the image.
- Paid accounts may set `FIXLOOP_CURSOR_MODEL` to another model enabled for
  that Cursor account.

The AkashML custom OpenAI base-URL spike failed for headless Cursor: the
Desktop custom model `zai-org/GLM-5.2` was rejected by the CLI model catalog.
Do not build a compatibility proxy during the hackathon. Use AkashML for P1
triage and held-back probe generation, while Cursor `auto` performs coding.

## Verification evidence

On 2026-07-17, the real Cursor `auto` worker fixed seeded issue 1 in 63 seconds.
The existing verifier accepted the result with:

- exact two-commit linear history;
- genuine regression failure on the base;
- two passes on the fixed code;
- no suite regressions; and
- all three held-back metamorphic probes passing.

The worker image builds as `fixloop-worker:local`. Its runtime includes Python
3.12, Cursor CLI, Git/GitHub CLI, Node/Corepack, npm/pnpm/Yarn/Bun,
uv/Poetry/Pipenv, and pytest. The worker test suite passes inside that image.

## Commands

```bash
python -m pytest -q worker/tests
docker build -f infra/Dockerfile -t fixloop-worker:local .
python -m worker --target /jobs/abc/target --issue-number 42 \
  --issue-file /jobs/abc/issue.txt --reason-codes METAMORPHIC_FAIL
```
