# worker — P2 owns this

Cursor-agent harness. Input: cloned target repo + issue text. Output: a
branch `fixloop/issue-{n}` shaped EXACTLY:

    base ──▶ test_commit ──▶ fix_commit

(test commit touches only tests/, fix commit touches only src/ — the
verifier enforces this via git parentage and rejects anything else with
NONLINEAR_SUBMISSION. Post-run, self-check with `git rev-parse` and do one
self-repair attempt before handing back.)

T+0 spike (do first): can cursor-agent point at AkashML's OpenAI-compatible
endpoint? Fallback if not: Cursor models run the loop, AkashML does triage +
holdback probe generation.

Retry contract: service passes verdict reason_codes from a rejected attempt;
condition attempt 2 on them (e.g. METAMORPHIC_FAIL → "your fix special-cases
the repro; fix the general case"). Budget: 2 verifier runs per issue.

Entry point to implement: `run(target_dir, issue_number, issue_text,
reason_codes=None) -> branch_name`, invoked from service/app.py:run_agent.
