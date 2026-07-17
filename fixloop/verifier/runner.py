"""Test execution stages. All runs happen in verifier-owned worktrees using
junit XML as the parse format -- never stdout scraping, never agent-reported
results.

Key distinction throughout: "test failed its assertion" (exit 1, <failure>)
vs "harness crashed" (collection error, <error>). A syntax-error test also
"fails" on base; only a genuine assertion failure proves the test captures
the bug.
"""

import subprocess
import tempfile
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path


def run_pytest(cwd, targets, cfg, extra=None):
    """Run pytest on targets, return parsed junit results."""
    junit = Path(tempfile.mkstemp(suffix=".xml")[1])
    cmd = cfg.get("pytest_cmd", ["python3", "-m", "pytest"]) + [
        "-q", "--tb=short", f"--junitxml={junit}",
        "-p", "no:cacheprovider",
    ] + (extra or []) + [str(t) for t in targets]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=cfg.get("timeout_s", 300))
    results = parse_junit(junit)
    junit.unlink(missing_ok=True)
    return proc.returncode, results, proc.stdout[-2000:]


def parse_junit(path):
    """-> {test_id: 'passed'|'failed'|'error'|'skipped'}"""
    out = {}
    if not Path(path).exists() or Path(path).stat().st_size == 0:
        return out
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        tid = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        if tc.find("error") is not None:
            out[tid] = "error"
        elif tc.find("failure") is not None:
            out[tid] = "failed"
        elif tc.find("skipped") is not None:
            out[tid] = "skipped"
        else:
            out[tid] = "passed"
    return out


def expect_fail(worktree, new_tests, cfg):
    """Stage 2: at base+test, the new test must fail its assertion.
    Proves the test actually captures the reported bug."""
    rc, results, tail = run_pytest(worktree, new_tests, cfg)
    if not results:
        return {"ok": False, "code": "NEW_TEST_NOT_DISCOVERED", "results": results,
                "log_tail": tail}
    if any(v == "error" for v in results.values()) or rc >= 2:
        return {"ok": False, "code": "NEW_TEST_HARNESS_ERROR", "results": results,
                "log_tail": tail}
    if any(v == "skipped" for v in results.values()):
        return {"ok": False, "code": "NEW_TEST_SKIPPED", "results": results}
    if all(v == "passed" for v in results.values()):
        return {"ok": False, "code": "TEST_PASSES_ON_BASE", "results": results,
                "detail": "test does not capture the bug -- passes without the fix"}
    return {"ok": True, "results": results}


def expect_pass_twice(worktree, new_tests, cfg):
    """Stage 3: at fix, the new test must pass, twice back-to-back.
    A flaky regression test is a liability merged into someone's repo."""
    runs = []
    for i in (1, 2):
        rc, results, tail = run_pytest(worktree, new_tests, cfg)
        runs.append(results)
        bad = [t for t, v in results.items() if v != "passed"]
        if bad:
            code = "NEW_TEST_FLAKY" if i == 2 else "TEST_FAILS_ON_FIX"
            return {"ok": False, "code": code, "runs": runs, "failing": bad,
                    "log_tail": tail}
    return {"ok": True, "runs": runs}


def suite_compare(wt_base, wt_fix, cfg):
    """Stage 4: full suite on base and fix; report regressions the fix
    introduced, not pre-existing reds. Quarantined flaky tests get one
    retry; everything else is deterministic."""
    quarantine = set(cfg.get("flaky_quarantine", []))
    suite_target = cfg.get("suite_target", "tests")

    _, base_r, _ = run_pytest(wt_base, [suite_target], cfg)
    _, fix_r, _ = run_pytest(wt_fix, [suite_target], cfg)

    regressions, retried = [], []
    for tid, status in fix_r.items():
        if status in ("failed", "error") and base_r.get(tid) == "passed":
            if tid in quarantine:
                _, retry, _ = run_pytest(wt_fix, [suite_target], cfg, extra=["-k", tid.split("::")[-1]])
                retried.append(tid)
                if all(v == "passed" for v in retry.values()):
                    continue
            regressions.append(tid)

    pre_existing = sorted(t for t, s in base_r.items() if s in ("failed", "error"))
    return {
        "base_total": len(base_r),
        "fix_total": len(fix_r),
        "regressions": regressions,
        "pre_existing_failures": pre_existing,
        "flake_retries": retried,
    }


def run_holdback(wt_fix, holdback_dir, cfg):
    """Stage 5a: metamorphic probes. Perturbed variants of the repro,
    generated at triage time and NEVER shown to the agent. If the exact
    repro passes but perturbations fail, the fix special-cased the input."""
    probe_dir = Path(wt_fix) / ".holdback_probe"
    probe_dir.mkdir(exist_ok=True)
    try:
        for f in Path(holdback_dir).glob("test_*.py"):
            shutil.copy(f, probe_dir / f.name)
        rc, results, tail = run_pytest(wt_fix, [probe_dir], cfg)
        failing = sorted(t for t, v in results.items() if v != "passed")
        return {"ok": not failing and bool(results), "total": len(results),
                "failing": failing}
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
