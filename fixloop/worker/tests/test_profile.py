from pathlib import Path

import pytest

from worker.errors import WorkerError
from worker.profile import profile_repository


def test_profiles_dependency_free_python_repo(git_repo):
    repo = git_repo({
        "src/mathish.py": "def double(n): return n + n\n",
        "tests/test_mathish.py": "from src.mathish import double\n\ndef test_double(): assert double(2) == 4\n",
    })

    profile = profile_repository(repo, "double returns wrong result")

    assert profile.languages == ["python"]
    assert profile.install_commands == []
    assert profile.source_roots == ["src"]
    assert profile.test_roots == ["tests"]
    assert profile.test_commands[0].argv[:3] == ("python3", "-m", "pytest")


def test_profiles_typescript_workspace_with_locked_install(git_repo):
    repo = git_repo({
        "package.json": '{"private":true,"workspaces":["packages/*"],"scripts":{"test":"vitest run"},"devDependencies":{"vitest":"1.0.0"}}',
        "package-lock.json": '{}',
        "packages/api/package.json": '{"name":"@demo/api"}',
        "packages/api/src/index.ts": "export const ok = true;\n",
        "packages/api/test/index.test.ts": "import { ok } from '../src';\n",
        "packages/web/package.json": '{"name":"@demo/web"}',
        "packages/web/src/index.ts": "export const page = true;\n",
    })

    profile = profile_repository(repo, "api response is incorrect")

    assert profile.languages == ["typescript"]
    assert profile.workspaces == ["packages/api", "packages/web"]
    assert profile.affected_workspaces == ["packages/api"]
    assert profile.install_commands[0].display() == "npm ci"
    assert "packages/api/src" in profile.source_roots
    assert "packages/api/test" in profile.test_roots


def test_profiles_mixed_uv_and_pnpm_repo(git_repo):
    repo = git_repo({
        "pyproject.toml": '[project]\nname="mixed"\ndependencies=["httpx"]\n',
        "uv.lock": "version = 1\n",
        "python_pkg/core.py": "VALUE = 1\n",
        "tests/test_core.py": "from python_pkg.core import VALUE\n\ndef test_value(): assert VALUE == 1\n",
        "package.json": '{"scripts":{"test":"vitest run"},"devDependencies":{"vitest":"1"}}',
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        "ui/src/main.ts": "export const value = 1;\n",
        "ui/test/main.test.ts": "test('value', () => {});\n",
    })
    profile = profile_repository(repo)
    assert profile.languages == ["python", "typescript"]
    assert [c.argv[0] for c in profile.install_commands] == ["uv", "pnpm"]
    assert len(profile.test_commands) == 2


def test_rejects_unlocked_dependencies(git_repo):
    repo = git_repo({
        "pyproject.toml": '[project]\nname="no-lock"\ndependencies=["requests"]\n',
        "src/app.py": "VALUE = 1\n",
        "tests/test_app.py": "def test_app(): assert True\n",
    })
    with pytest.raises(WorkerError, match="UNSUPPORTED_REPOSITORY"):
        profile_repository(repo)
