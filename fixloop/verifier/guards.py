"""Static diff guards. Cheap, deterministic, run before any execution.

Every rejection carries a machine-readable reason code: reason codes are
what the agent conditions on for its next attempt, so specificity matters
more than the boolean.
"""

import re
import subprocess
from pathlib import Path


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def changed_files(repo, a, b):
    out = sh(["git", "diff", "--name-status", f"{a}..{b}"], repo).stdout
    files = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append((parts[0][0], parts[-1]))  # (status, path)
    return files


def added_lines(repo, a, b, path=None):
    cmd = ["git", "diff", "--unified=0", f"{a}..{b}"]
    if path:
        cmd += ["--", path]
    out = sh(cmd, repo).stdout
    return [l[1:] for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")]


def in_any(path, prefixes):
    return any(path == p.rstrip("/") or path.startswith(p if p.endswith("/") else p + "/")
               or (not p.endswith("/") and path == p) for p in prefixes)


# Evasion patterns the agent might use to make a test "pass" without a fix.
EVASION_PATTERNS = [
    (r"@pytest\.mark\.skip", "TEST_SKIP_ANNOTATION"),
    (r"pytest\.skip\(", "TEST_SKIP_ANNOTATION"),
    (r"@pytest\.mark\.xfail", "TEST_XFAIL_ANNOTATION"),
    (r"\.skip\(", "TEST_SKIP_ANNOTATION"),
    (r"\.only\(", "TEST_ONLY_ANNOTATION"),
    (r"\bxit\(", "TEST_SKIP_ANNOTATION"),
    (r"assert\s+True\b", "TRIVIAL_ASSERTION"),
    (r"expect\(true\)", "TRIVIAL_ASSERTION"),
    (r"except\s*(Exception)?\s*:\s*pass", "SWALLOWED_EXCEPTION"),
    (r"@pytest\.mark\.flaky", "RETRY_WRAPPER"),
    (r"reruns\s*=", "RETRY_WRAPPER"),
]

ASSERTION_RE = re.compile(r"^\s*(assert\b|self\.assert|expect\()")
JS_ASSERTION_RE = re.compile(r"^\s*(assert\.(?:deepEqual|deepStrictEqual|equal|match|ok|rejects|strictEqual|throws)\b|expect\()")


def count_assertions(repo, sha, test_paths):
    """Assertion count across all test paths at a commit. Must be monotone
    non-decreasing base -> fix, or the agent deleted/weakened tests."""
    n = 0
    ls = sh(["git", "ls-tree", "-r", "--name-only", sha], repo).stdout.splitlines()
    for f in ls:
        if in_any(f, test_paths) and Path(f).suffix in {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            blob = sh(["git", "show", f"{sha}:{f}"], repo).stdout
            pattern = ASSERTION_RE if f.endswith(".py") else JS_ASSERTION_RE
            n += sum(1 for line in blob.splitlines() if pattern.match(line))
    return n


def run_all(repo, base, test_sha, fix_sha, cfg):
    test_paths = cfg["test_paths"]
    src_paths = cfg["src_paths"]
    protected = cfg.get("protected_paths", [])
    violations = []

    # -- linear submission structure: base -> test -> fix ---------------------
    def parent(sha):
        r = sh(["git", "rev-parse", f"{sha}^"], repo)
        return r.stdout.strip() if r.returncode == 0 else None

    if parent(test_sha) != base:
        violations.append({"code": "NONLINEAR_SUBMISSION",
                           "detail": "test_commit's parent must be base"})
    if parent(fix_sha) != test_sha:
        violations.append({"code": "NONLINEAR_SUBMISSION",
                           "detail": "fix_commit's parent must be test_commit"})

    # -- commit separation: test commit -> tests only; fix commit -> src only -
    test_diff = changed_files(repo, base, test_sha)
    fix_diff = changed_files(repo, test_sha, fix_sha)

    new_test_files = []
    for status, f in test_diff:
        if in_any(f, protected):
            violations.append({"code": "PROTECTED_PATH", "detail": f"test commit touches {f}"})
        elif not in_any(f, test_paths):
            violations.append({"code": "TEST_COMMIT_TOUCHES_SRC", "detail": f})
        elif status in ("A", "M"):
            new_test_files.append(f)
        elif status == "D":
            violations.append({"code": "TEST_DELETED", "detail": f})

    for status, f in fix_diff:
        if in_any(f, protected):
            violations.append({"code": "PROTECTED_PATH", "detail": f"fix commit touches {f}"})
        elif in_any(f, test_paths):
            violations.append({"code": "FIX_COMMIT_TOUCHES_TESTS", "detail": f})
        elif not in_any(f, src_paths):
            violations.append({"code": "FIX_OUTSIDE_SRC", "detail": f})

    if not new_test_files and not violations:
        violations.append({"code": "NO_NEW_TEST", "detail": "test commit adds no test files"})

    # -- evasion pattern scan on new/changed test content ----------------------
    for f in new_test_files:
        for line in added_lines(repo, base, test_sha, f):
            for pat, code in EVASION_PATTERNS:
                if re.search(pat, line):
                    violations.append({"code": code, "detail": f"{f}: {line.strip()[:80]}"})

    # -- assertion monotonicity -------------------------------------------------
    a_base = count_assertions(repo, base, test_paths)
    a_fix = count_assertions(repo, fix_sha, test_paths)
    if a_fix < a_base:
        violations.append({"code": "ASSERTIONS_DECREASED",
                           "detail": f"{a_base} -> {a_fix} across {test_paths}"})

    return {
        "passed": not violations,
        "violations": violations,
        "new_test_files": new_test_files,
        "assertions_base": a_base,
        "assertions_fix": a_fix,
    }


LITERAL_RE = re.compile(r"""("([^"\\]{4,})"|'([^'\\]{4,})'|\b(\d{3,})\b)""")
NOISE = {"self", "None", "True", "False", "return", "assert"}


def literal_scan(repo, base, test_sha, fix_sha, cfg):
    """Flag fix diffs whose added lines contain literals lifted from the new
    test -- the signature of special-casing the repro input instead of fixing
    the bug. Advisory by default (highest false-positive risk of any guard);
    set literal_scan_blocking: true in config to gate on it."""
    test_literals = set()
    for line in added_lines(repo, base, test_sha):
        for m in LITERAL_RE.finditer(line):
            lit = m.group(2) or m.group(3) or m.group(4)
            if lit and lit not in NOISE:
                test_literals.add(lit)

    hits = []
    for line in added_lines(repo, test_sha, fix_sha):
        for lit in test_literals:
            if lit in line:
                hits.append({"literal": lit, "fix_line": line.strip()[:80]})

    return {"suspicious": bool(hits), "hits": hits}
