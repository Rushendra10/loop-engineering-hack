"""Fast, deterministic Python/TypeScript repository profiling."""

from __future__ import annotations

import json
import os
import re
import shlex
try:
    import tomllib
except ModuleNotFoundError:  # Local macOS Python; worker image uses Python 3.12.
    import tomli as tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import WorkerError


IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist",
    "build", "coverage", ".next", ".turbo", ".cache", "__pycache__",
}
PY_LOCKS = {"uv.lock", "poetry.lock", "Pipfile.lock"}
JS_LOCKS = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb"}
PROTECTED_NAMES = PY_LOCKS | JS_LOCKS | {
    "package.json", "pyproject.toml", "Pipfile", "tsconfig.json",
    "requirements.txt", "setup.py", "setup.cfg", "tox.ini", "pytest.ini",
    "pnpm-workspace.yaml", "nx.json", "turbo.json",
}


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: str = "."

    def display(self) -> str:
        command = shlex.join(self.argv)
        return command if self.cwd == "." else f"cd {shlex.quote(self.cwd)} && {command}"


@dataclass
class RepositoryProfile:
    languages: list[str]
    workspaces: list[str]
    affected_workspaces: list[str]
    install_commands: list[Command]
    test_commands: list[Command]
    source_roots: list[str]
    test_roots: list[str]
    protected_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "languages": self.languages,
            "workspaces": self.workspaces,
            "affected_workspaces": self.affected_workspaces,
            "install_commands": [c.display() for c in self.install_commands],
            "test_commands": [c.display() for c in self.test_commands],
            "source_roots": self.source_roots,
            "test_roots": self.test_roots,
        }


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".git"))
        base = Path(current)
        found.extend(base / name for name in sorted(names))
    return found


def _relative(path: Path, root: Path) -> str:
    value = path.relative_to(root).as_posix()
    return value or "."


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _workspace_patterns(package: dict) -> list[str]:
    raw = package.get("workspaces", [])
    if isinstance(raw, dict):
        raw = raw.get("packages", [])
    return [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []


def _workspace_dirs(root: Path, files: list[Path]) -> list[str]:
    result: set[str] = set()
    manifests = [p for p in files if p.name in {"package.json", "pyproject.toml"}]
    if len(manifests) > 1:
        result.update(_relative(p.parent, root) for p in manifests)

    root_package = _read_json(root / "package.json")
    for pattern in _workspace_patterns(root_package):
        for candidate in root.glob(pattern):
            if candidate.is_dir():
                result.add(_relative(candidate, root))

    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.exists():
        for line in pnpm.read_text(errors="ignore").splitlines():
            match = re.match(r"\s*-\s*['\"]?([^'\"#]+)", line)
            if match:
                for candidate in root.glob(match.group(1).strip()):
                    if candidate.is_dir():
                        result.add(_relative(candidate, root))

    result.discard(".")
    return sorted(result)


def _affected(workspaces: list[str], issue_text: str) -> list[str]:
    if not workspaces:
        return []
    haystack = issue_text.casefold()
    scored: list[tuple[int, str]] = []
    for workspace in workspaces:
        tokens = {part for part in re.split(r"[/_.-]+", workspace.casefold()) if len(part) > 2}
        score = sum(1 for token in tokens if token in haystack)
        scored.append((score, workspace))
    best = max(score for score, _ in scored)
    return sorted(path for score, path in scored if score == best and score > 0) or workspaces


def _python_has_dependencies(pyproject: Path) -> bool:
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return True
    project_deps = data.get("project", {}).get("dependencies", [])
    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    poetry_deps = {k: v for k, v in poetry_deps.items() if k.casefold() != "python"}
    return bool(project_deps or poetry_deps)


def _install_commands(root: Path, files: list[Path], languages: list[str]) -> list[Command]:
    commands: list[Command] = []

    py_lock_paths = [p for p in files if p.name in PY_LOCKS]
    py_manifests = [
        p for p in files
        if p.name in {"pyproject.toml", "Pipfile"}
        or (p.name.startswith("requirements") and p.suffix == ".txt")
    ]
    python_needs_install = any(
        p.name != "pyproject.toml" or _python_has_dependencies(p) for p in py_manifests
    )
    if "python" in languages and python_needs_install and not py_lock_paths:
        raise WorkerError("UNSUPPORTED_REPOSITORY", "Python dependencies are declared without a supported lockfile")
    for lock in sorted(py_lock_paths):
        cwd = _relative(lock.parent, root)
        if lock.name == "uv.lock":
            commands.append(Command(("uv", "sync", "--frozen"), cwd))
        elif lock.name == "poetry.lock":
            commands.append(Command(("poetry", "install", "--no-interaction"), cwd))
        else:
            commands.append(Command(("pipenv", "sync"), cwd))

    packages = [p for p in files if p.name == "package.json"]
    js_lock_paths = [p for p in files if p.name in JS_LOCKS]
    packages_with_deps = [
        p for p in packages
        if _read_json(p).get("dependencies") or _read_json(p).get("devDependencies")
    ]
    if "typescript" in languages and packages_with_deps and not js_lock_paths:
        raise WorkerError("UNSUPPORTED_REPOSITORY", "JavaScript dependencies are declared without a supported lockfile")

    # A root workspace lock covers its child packages; nested independent locks remain separate.
    root_js_lock = next((p for p in js_lock_paths if p.parent == root), None)
    selected_js_locks = [root_js_lock] if root_js_lock else js_lock_paths
    for lock in sorted(p for p in selected_js_locks if p is not None):
        cwd = _relative(lock.parent, root)
        if lock.name == "package-lock.json":
            commands.append(Command(("npm", "ci"), cwd))
        elif lock.name == "pnpm-lock.yaml":
            commands.append(Command(("pnpm", "install", "--frozen-lockfile"), cwd))
        elif lock.name == "yarn.lock":
            package_manager = _read_json(lock.parent / "package.json").get("packageManager", "")
            flag = "--frozen-lockfile" if package_manager.startswith("yarn@1.") else "--immutable"
            commands.append(Command(("yarn", "install", flag), cwd))
        else:
            commands.append(Command(("bun", "install", "--frozen-lockfile"), cwd))
    return commands


def _test_commands(root: Path, files: list[Path], languages: list[str]) -> list[Command]:
    commands: list[Command] = []
    if "python" in languages:
        py_tests = [p for p in files if p.suffix == ".py" and (p.name.startswith("test_") or "/tests/" in f"/{_relative(p, root)}/")]
        pytest_config = any(p.name in {"pytest.ini", "tox.ini", "pyproject.toml", "setup.cfg"} for p in files)
        if py_tests or pytest_config:
            commands.append(Command(("python3", "-m", "pytest", "-q", "-p", "no:cacheprovider")))

    if "typescript" in languages:
        root_package = root / "package.json"
        all_packages = [p for p in files if p.name == "package.json"]
        root_has_test = bool(_read_json(root_package).get("scripts", {}).get("test")) if root_package.exists() else False
        package_candidates = [root_package] if root_has_test else all_packages
        for package_path in package_candidates:
            package = _read_json(package_path)
            test_script = package.get("scripts", {}).get("test")
            if not test_script:
                continue
            cwd = _relative(package_path.parent, root)
            lock_names = {p.name for p in files if p.parent == package_path.parent}
            root_lock_names = {p.name for p in files if p.parent == root}
            if {"bun.lock", "bun.lockb"} & (lock_names | root_lock_names):
                argv = ("bun", "run", "test")
            elif "pnpm-lock.yaml" in lock_names or (root / "pnpm-lock.yaml").exists():
                argv = ("pnpm", "test")
            elif "yarn.lock" in lock_names or (root / "yarn.lock").exists():
                argv = ("yarn", "test")
            else:
                argv = ("npm", "test", "--")
            commands.append(Command(argv, cwd))
    return commands


def _roots(root: Path, files: list[Path]) -> tuple[list[str], list[str]]:
    source: set[str] = set()
    tests: set[str] = set()
    for path in files:
        rel = _relative(path, root)
        parts = Path(rel).parts
        lower_parts = [part.casefold() for part in parts]
        is_test = (
            any(part in {"test", "tests", "__tests__", "spec", "specs", "fixtures"} for part in lower_parts[:-1])
            or path.name.startswith("test_")
            or bool(re.search(r"\.(test|spec)\.[cm]?[jt]sx?$", path.name))
        )
        if is_test:
            if len(parts) > 1:
                index = next((i for i, part in enumerate(lower_parts[:-1]) if part in {"test", "tests", "__tests__", "spec", "specs", "fixtures"}), len(parts) - 2)
                tests.add(Path(*parts[: index + 1]).as_posix())
            continue
        if path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
            continue
        if "src" in lower_parts[:-1]:
            source.add(Path(*parts[: lower_parts.index("src") + 1]).as_posix())
        elif len(parts) > 1:
            source.add(parts[0])
        else:
            source.add(".")

    # These defaults let Cursor add the first regression test in a conventional location.
    if not tests:
        tests.add("tests")
    return sorted(source), sorted(tests)


def profile_repository(root: Path, issue_text: str = "") -> RepositoryProfile:
    root = Path(root).resolve()
    if not (root / ".git").exists():
        raise WorkerError("INVALID_REPOSITORY", f"{root} is not a Git working tree")
    files = _files(root)
    names = {p.name for p in files}
    languages: list[str] = []
    if names & ({"pyproject.toml", "uv.lock", "poetry.lock", "Pipfile.lock", "pytest.ini"}) or any(p.suffix == ".py" for p in files):
        languages.append("python")
    if names & ({"package.json", "tsconfig.json"} | JS_LOCKS) or any(p.suffix in {".ts", ".tsx"} for p in files):
        languages.append("typescript")
    if not languages:
        raise WorkerError("UNSUPPORTED_REPOSITORY", "no Python or TypeScript project was detected")

    workspaces = _workspace_dirs(root, files)
    source_roots, test_roots = _roots(root, files)
    test_commands = _test_commands(root, files, languages)
    if not source_roots or not test_commands:
        raise WorkerError("UNSUPPORTED_REPOSITORY", "could not discover both source code and runnable tests")

    protected = sorted(
        _relative(p, root)
        for p in files
        if p.name in PROTECTED_NAMES
        or (p.name.startswith("requirements") and p.suffix == ".txt")
        or p.name.startswith(".env")
        or re.search(r"(^|\.)(jest|vitest|eslint|prettier|babel|webpack)\.config\.[cm]?[jt]s$", p.name)
    )
    return RepositoryProfile(
        languages=languages,
        workspaces=workspaces,
        affected_workspaces=_affected(workspaces, issue_text),
        install_commands=_install_commands(root, files, languages),
        test_commands=test_commands,
        source_roots=source_roots,
        test_roots=test_roots,
        protected_paths=protected,
    )
