"""Deterministic two-phase autonomous worker."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from .cursor import CursorRunner
from .errors import WorkerError
from .gitops import (
    changed_paths,
    commit_paths,
    normalize_cursor_commits,
    path_in_roots,
    prepare_branch,
    resolve_base,
    verify_contract,
)
from .profile import Command, RepositoryProfile, profile_repository


SECRET_WORDS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}

RETRY_GUIDANCE = {
    "NONLINEAR_SUBMISSION": "Keep strict test/source path separation; the harness owns every Git operation.",
    "TEST_COMMIT_TOUCHES_SRC": "The regression-test phase may edit only tests and fixtures.",
    "FIX_COMMIT_TOUCHES_TESTS": "The fix phase may not edit tests or fixtures.",
    "NO_NEW_TEST": "Add a real regression test with a meaningful assertion.",
    "TEST_PASSES_ON_BASE": "Write a regression test whose assertion genuinely fails on the unfixed code.",
    "TRIVIAL_ASSERTION": "Use a meaningful behavioral assertion, not a constant or tautology.",
    "TEST_SKIP_ANNOTATION": "Do not skip, xfail, focus, or weaken the regression test.",
    "TEST_FAILS_ON_FIX": "Correct the root cause and avoid collateral regressions.",
    "SUITE_REGRESSION": "Minimize the fix and preserve all existing behavior outside the issue.",
    "METAMORPHIC_FAIL": "Remove input special-casing and implement the general behavior described by the issue.",
    "LITERAL_OVERFIT": "Do not copy repro literals into production code; generalize the implementation.",
    "NEW_TEST_FLAKY": "Remove timing, randomness, and shared-state dependence.",
}


def _scrubbed_env() -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not any(word in key.upper() for word in SECRET_WORDS)
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CI"] = "1"
    return env


def retry_guidance(reason_codes: list[str]) -> str:
    if not reason_codes:
        return ""
    lines = [RETRY_GUIDANCE.get(code, f"Inspect the previous attempt critically for verifier failure {code}.") for code in reason_codes]
    return "This is verifier attempt 2. Address these findings:\n- " + "\n- ".join(lines)


class Worker:
    def __init__(
        self,
        target_dir: Path,
        issue_number: int,
        issue_text: str,
        reason_codes: list[str] | None = None,
        cursor_runner: Callable[[Path, str, float, str], None] | None = None,
        deadline_s: float = 900,
    ):
        self.target = Path(target_dir).resolve()
        self.issue_number = issue_number
        self.issue_text = issue_text
        self.reason_codes = reason_codes or []
        self.cursor = cursor_runner or CursorRunner()
        self.deadline_s = deadline_s
        self.started = time.monotonic()
        self.result_path = self.target.parent / "worker-result.json"
        self.repair_used = False

    def _remaining(self) -> float:
        remaining = self.deadline_s - (time.monotonic() - self.started)
        if remaining <= 0:
            raise WorkerError("DEADLINE_EXCEEDED", "the 15-minute worker deadline expired")
        return remaining

    def _write_result(self, **values) -> None:
        duration = round(time.monotonic() - self.started, 3)
        document = {**values, "duration_s": duration}
        self.result_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    def _run_command(self, command: Command, extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        cwd = self.target if command.cwd == "." else self.target / command.cwd
        try:
            return subprocess.run(
                [*command.argv, *(extra or [])],
                cwd=cwd,
                env=_scrubbed_env(),
                capture_output=True,
                text=True,
                timeout=max(1, min(300, self._remaining())),
            )
        except FileNotFoundError as exc:
            raise WorkerError("TOOLCHAIN_UNAVAILABLE", f"{command.argv[0]} is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkerError("COMMAND_TIMEOUT", command.display()) from exc

    def _install(self, profile: RepositoryProfile) -> None:
        for command in profile.install_commands:
            proc = self._run_command(command)
            if proc.returncode:
                raise WorkerError("INSTALL_FAILED", f"{command.display()} exited {proc.returncode}")

    def _target_commands(self, profile: RepositoryProfile, tests: list[str]) -> list[tuple[Command, list[str]]]:
        commands: list[tuple[Command, list[str]]] = []
        py_tests = [p for p in tests if p.endswith(".py")]
        js_tests = [p for p in tests if Path(p).suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}]
        for command in profile.test_commands:
            tool = command.argv[0]
            selected = py_tests if "pytest" in command.argv else js_tests
            if not selected:
                continue
            adjusted: list[str] = []
            for path in selected:
                if command.cwd != "." and path.startswith(command.cwd.rstrip("/") + "/"):
                    adjusted.append(path[len(command.cwd.rstrip("/")) + 1 :])
                else:
                    adjusted.append(path)
            commands.append((command, adjusted))
        return commands

    def _tests_fail(self, profile: RepositoryProfile, tests: list[str]) -> None:
        commands = self._target_commands(profile, tests)
        if not commands:
            raise WorkerError("NEW_TEST_NOT_DISCOVERED", "no test command matches the changed test files")
        results = [self._run_command(command, paths) for command, paths in commands]
        if any(proc.returncode >= 2 for proc in results):
            raise WorkerError("NEW_TEST_HARNESS_ERROR", "the new regression test could not run cleanly")
        if all(proc.returncode == 0 for proc in results):
            raise WorkerError("TEST_PASSES_ON_BASE", "the new test passes without the fix")

    def _tests_pass(self, profile: RepositoryProfile, tests: list[str]) -> None:
        commands = self._target_commands(profile, tests)
        for run_number in (1, 2):
            for command, paths in commands:
                proc = self._run_command(command, paths)
                if proc.returncode:
                    code = "TEST_FAILS_ON_FIX" if run_number == 1 else "NEW_TEST_FLAKY"
                    raise WorkerError(code, f"{command.display()} exited {proc.returncode}")

    def _suite_passes(self, profile: RepositoryProfile) -> None:
        for command in profile.test_commands:
            proc = self._run_command(command)
            if proc.returncode:
                raise WorkerError("SUITE_REGRESSION", f"{command.display()} exited {proc.returncode}")

    @staticmethod
    def _test_paths_valid(paths: list[str], profile: RepositoryProfile) -> bool:
        return bool(paths) and all(
            path not in profile.protected_paths and path_in_roots(path, profile.test_roots)
            for path in paths
        )

    @staticmethod
    def _fix_paths_valid(paths: list[str], profile: RepositoryProfile) -> bool:
        return bool(paths) and all(
            path not in profile.protected_paths
            and Path(path).suffix in SOURCE_SUFFIXES
            and path_in_roots(path, profile.source_roots)
            and not path_in_roots(path, profile.test_roots)
            for path in paths
        )

    def _invoke(self, prompt: str, phase: str, expected_head: str) -> list[str]:
        self.cursor(self.target, prompt, self._remaining(), phase)
        normalize_cursor_commits(self.target, expected_head)
        return changed_paths(self.target)

    def _repair(self, phase: str, expected_head: str, allowed: list[str], prohibited: str) -> list[str]:
        if self.repair_used:
            raise WorkerError(f"{phase.upper()}_PATH_VIOLATION", "Cursor edited forbidden paths after repair")
        self.repair_used = True
        prompt = (
            f"Repair the current {phase} working-tree changes. Keep the intended behavior but edit only: {allowed}. "
            f"Remove or revert every {prohibited} change. Do not commit, push, or change dependencies/configuration."
        )
        return self._invoke(prompt, f"{phase}-repair", expected_head)

    def _test_prompt(self, profile: RepositoryProfile) -> str:
        return f"""You are phase 1 of an autonomous bug-fix worker.
Issue #{self.issue_number}:
{self.issue_text}

Repository profile: {json.dumps(profile.to_dict(), sort_keys=True)}
{retry_guidance(self.reason_codes)}

Understand the issue and add the smallest meaningful regression test that fails by assertion on the current code.
You may edit only these test/fixture roots: {profile.test_roots}.
Do not edit source, manifests, lockfiles, configuration, or existing behavior. Do not skip/xfail/focus the test.
Do not run git, commit, push, create branches, or install dependencies. Leave file changes in the working tree."""

    def _fix_prompt(self, profile: RepositoryProfile, tests: list[str]) -> str:
        return f"""You are phase 2 of an autonomous bug-fix worker.
Issue #{self.issue_number}:
{self.issue_text}

The regression tests are {tests}. Implement the smallest general root-cause fix so they pass.
Repository profile: {json.dumps(profile.to_dict(), sort_keys=True)}
{retry_guidance(self.reason_codes)}

You may edit production source only under: {profile.source_roots}. Coordinated edits across affected workspaces are allowed.
Do not edit tests, fixtures, manifests, lockfiles, or configuration. Do not special-case repro literals.
Do not run git, commit, push, create branches, or install dependencies. Leave file changes in the working tree."""

    def execute(self) -> str:
        profile: RepositoryProfile | None = None
        branch = f"fixloop/issue-{self.issue_number}"
        try:
            profile = profile_repository(self.target, self.issue_text)
            base = resolve_base(self.target, branch)
            prepare_branch(self.target, branch, base)
            self._install(profile)

            test_paths = self._invoke(self._test_prompt(profile), "test", base)
            if not self._test_paths_valid(test_paths, profile):
                test_paths = self._repair("test", base, profile.test_roots, "source/config/dependency")
            if not self._test_paths_valid(test_paths, profile):
                raise WorkerError("TEST_PATH_VIOLATION", ", ".join(test_paths) or "no test changes")
            self._tests_fail(profile, test_paths)
            test_sha = commit_paths(self.target, test_paths, f"test: reproduce issue #{self.issue_number}")

            fix_paths = self._invoke(self._fix_prompt(profile, test_paths), "fix", test_sha)
            if not self._fix_paths_valid(fix_paths, profile):
                fix_paths = self._repair("fix", test_sha, profile.source_roots, "test/config/dependency")
            if not self._fix_paths_valid(fix_paths, profile):
                raise WorkerError("FIX_PATH_VIOLATION", ", ".join(fix_paths) or "no source changes")

            self._tests_pass(profile, test_paths)
            self._suite_passes(profile)
            fix_sha = commit_paths(self.target, fix_paths, f"fix: resolve issue #{self.issue_number}")
            verify_contract(self.target, base, test_sha, fix_sha, profile.test_roots, profile.source_roots)
            self._write_result(
                status="completed",
                branch=branch,
                base_sha=base,
                test_sha=test_sha,
                fix_sha=fix_sha,
                attempt=2 if self.reason_codes else 1,
                profile=profile.to_dict(),
            )
            return branch
        except WorkerError as exc:
            self._write_result(
                status="failed",
                error={"code": exc.code, "detail": exc.detail},
                branch=branch,
                attempt=2 if self.reason_codes else 1,
                profile=profile.to_dict() if profile else None,
            )
            raise
        except Exception as exc:
            wrapped = WorkerError("INTERNAL_ERROR", str(exc)[:300])
            self._write_result(
                status="failed",
                error={"code": wrapped.code, "detail": wrapped.detail},
                branch=branch,
                attempt=2 if self.reason_codes else 1,
                profile=profile.to_dict() if profile else None,
            )
            raise wrapped from exc


def run(
    target_dir: Path,
    issue_number: int,
    issue_text: str,
    reason_codes: list[str] | None = None,
) -> str:
    """Run Cursor and return the deterministic fixloop branch name."""
    return Worker(target_dir, issue_number, issue_text, reason_codes).execute()
