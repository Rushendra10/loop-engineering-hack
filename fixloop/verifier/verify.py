#!/usr/bin/env python3
"""robobun-verifier: adversarial verification for agent-submitted bug fixes.

The verifier is the loop's ground truth. It assumes the agent will
(accidentally) reward-hack and is structured to catch that, not to trust
agent claims.

Submission contract (enforced, not assumed):
    base ──▶ test_commit ──▶ fix_commit        (linear, exactly this shape)
    test_commit touches only test paths; fix_commit touches only src paths.

Stages:
    1. diff guards        static policy checks (cheap, run first)
    2. fail-on-base       new test must FAIL at test_commit (= base + test)
    3. pass-on-fix        new test must PASS at fix_commit, twice
    4. suite + integrity  full suite, regressions vs cached base run
    5. probes             held-back metamorphic variants + literal scan
    6. verdict            single JSON blob; the ONLY state the loop trusts

Usage:
    verify.py --repo PATH --base SHA --test-commit SHA --fix-commit SHA \
              --config verifier.yml --out verdict.json [--issue ID]
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

import guards
import runner


def sh(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{r.stderr}")
    return r


class Worktrees:
    """Ephemeral read-only checkouts. The agent never had write access here."""

    def __init__(self, repo):
        self.repo = str(repo)
        self.root = Path(tempfile.mkdtemp(prefix="verifier-wt-"))
        self.created = []

    def at(self, sha, name):
        path = self.root / name
        sh(["git", "worktree", "add", "--detach", str(path), sha], cwd=self.repo)
        self.created.append(path)
        return path

    def cleanup(self):
        for p in self.created:
            sh(["git", "worktree", "remove", "--force", str(p)], cwd=self.repo, check=False)
        shutil.rmtree(self.root, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--test-commit", required=True)
    ap.add_argument("--fix-commit", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--issue", default="unknown")
    ap.add_argument("--holdback-root", default=None,
                    help="verifier-owned dir of held-back probe tests, never shown to the agent")
    args = ap.parse_args()

    t0 = time.time()
    cfg = yaml.safe_load(Path(args.config).read_text())
    repo = Path(args.repo).resolve()

    def resolve(ref):
        return sh(["git", "rev-parse", ref], cwd=repo).stdout.strip()

    base, test_sha, fix_sha = resolve(args.base), resolve(args.test_commit), resolve(args.fix_commit)

    verdict = {
        "issue": args.issue,
        "base_sha": base,
        "test_sha": test_sha,
        "fix_sha": fix_sha,
        "guards": None,
        "fails_on_base": None,
        "passes_on_fix": None,
        "suite": None,
        "probes": {"metamorphic": None, "literal_scan": None},
        "verdict": "rejected",
        "reason_codes": [],
        "duration_s": None,
    }

    def finish():
        verdict["duration_s"] = round(time.time() - t0, 1)
        Path(args.out).write_text(json.dumps(verdict, indent=2))
        print(json.dumps(verdict, indent=2))
        # exit 0 iff verified: lets CI step status mirror the verdict
        sys.exit(0 if verdict["verdict"] == "verified" else 1)

    # ---- stage 1: diff guards (static, no execution) ------------------------
    g = guards.run_all(repo, base, test_sha, fix_sha, cfg)
    verdict["guards"] = g
    if g["violations"]:
        verdict["reason_codes"] = [v["code"] for v in g["violations"]]
        finish()

    new_tests = g["new_test_files"]
    wts = Worktrees(repo)
    try:
        wt_base = wts.at(base, "base")
        wt_test = wts.at(test_sha, "base-plus-test")   # base + test only
        wt_fix = wts.at(fix_sha, "fix")

        # ---- stage 2: fail-on-base ------------------------------------------
        r = runner.expect_fail(wt_test, new_tests, cfg)
        verdict["fails_on_base"] = r
        if not r["ok"]:
            verdict["reason_codes"].append(r["code"])
            finish()

        # ---- stage 3: pass-on-fix (twice: cheap flake filter) ---------------
        r = runner.expect_pass_twice(wt_fix, new_tests, cfg)
        verdict["passes_on_fix"] = r
        if not r["ok"]:
            verdict["reason_codes"].append(r["code"])
            finish()

        # ---- stage 4: full suite, regressions vs base -----------------------
        r = runner.suite_compare(wt_base, wt_fix, cfg)
        verdict["suite"] = r
        if r["regressions"]:
            verdict["reason_codes"].append("SUITE_REGRESSION")
            finish()

        # ---- stage 5b first (static, cheap): literal scan --------------------
        ls = guards.literal_scan(repo, base, test_sha, fix_sha, cfg)
        verdict["probes"]["literal_scan"] = ls

        # ---- stage 5a: metamorphic probes (held back from the agent) --------
        hb_root = args.holdback_root or cfg.get("holdback_root")
        if hb_root:
            hb = Path(hb_root) / args.issue
            if hb.is_dir() and any(hb.glob("test_*.*")):
                r = runner.run_holdback(wt_fix, hb, cfg)
                verdict["probes"]["metamorphic"] = r
                if not r["ok"]:
                    verdict["reason_codes"].append("METAMORPHIC_FAIL")
                    verdict["verdict"] = "suspected_overfit"
                    finish()

        if ls["suspicious"] and cfg.get("literal_scan_blocking", False):
            verdict["reason_codes"].append("LITERAL_OVERFIT")
            verdict["verdict"] = "suspected_overfit"
            finish()

        verdict["verdict"] = "verified"
        finish()
    finally:
        wts.cleanup()


if __name__ == "__main__":
    main()
