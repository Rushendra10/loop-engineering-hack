import json
from pathlib import Path

import pytest

from service import app as service


def test_repo_slug_accepts_normal_github_urls():
    assert service.repo_slug("https://github.com/acme/widget") == "acme/widget"
    assert service.repo_slug("git@github.com:acme/widget.git") == "acme/widget"


def test_repo_slug_rejects_non_github_urls():
    with pytest.raises(RuntimeError, match="github.com"):
        service.repo_slug("https://example.com/acme/widget")


def test_run_job_reaches_verified_pr(monkeypatch, tmp_path):
    service.JOBS.clear()
    monkeypatch.setattr(service, "JOBS_DIR", tmp_path)

    def fake_command(command, cwd=None, timeout=120):
        if command[:2] == ["git", "clone"]:
            Path(command[-1]).mkdir(parents=True)
            return ""
        if command[:3] == ["git", "branch", "--show-current"]:
            return "main"
        raise AssertionError(command)

    def fake_agent(target, issue, issue_text, reason_codes=None, deadline_s=900):
        result = {
            "base_sha": "base",
            "test_sha": "test",
            "fix_sha": "fix",
            "profile": {"test_roots": ["tests"], "source_roots": ["src"]},
        }
        (target.parent / "worker-result.json").write_text(json.dumps(result))
        return "fixloop/issue-7"

    monkeypatch.setattr(service, "_run", fake_command)
    monkeypatch.setattr(service, "fetch_issue_text", lambda repo, issue: "broken widget")
    monkeypatch.setattr(service, "run_agent", fake_agent)
    monkeypatch.setattr(
        service,
        "run_verifier",
        lambda target, result, issue: {"verdict": "verified", "issue": issue, "reason_codes": []},
    )
    monkeypatch.setattr(service, "open_pr", lambda *args: "https://github.com/acme/widget/pull/1")

    service.JOBS["abc"] = {
        "status": "running",
        "repo": "https://github.com/acme/widget",
        "issue": 7,
        "verdict": None,
        "pr_url": None,
        "branch": None,
        "attempt": 1,
        "stage": "queued",
    }
    service.run_job("abc")

    assert service.JOBS["abc"]["status"] == "done"
    assert service.JOBS["abc"]["stage"] == "finished"
    assert service.JOBS["abc"]["branch"] == "fixloop/issue-7"
    assert service.JOBS["abc"]["pr_url"].endswith("/pull/1")


def test_open_pr_requires_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GH_TOKEN"):
        service.open_pr(tmp_path, "https://github.com/acme/widget", "fixloop/issue-1", "main", {})


def test_verifier_config_uses_profile_roots(monkeypatch, tmp_path):
    base = tmp_path / "base.yml"
    base.write_text("test_paths: [tests/]\nsrc_paths: [src/]\nsuite_target: tests\n")
    monkeypatch.setattr(service, "VERIFIER_CFG", base)
    output = tmp_path / "verdict.json"
    path = service._verifier_config(
        {"profile": {"test_roots": ["packages/api/tests"], "source_roots": ["packages/api/src"]}},
        output,
    )
    generated = path.read_text()
    assert "packages/api/tests" in generated
    assert "packages/api/src" in generated


def test_verifier_config_selects_node_for_javascript_profile(monkeypatch, tmp_path):
    base = tmp_path / "base.yml"
    base.write_text("test_paths: [tests/]\nsrc_paths: [src/]\nsuite_target: tests\n")
    monkeypatch.setattr(service, "VERIFIER_CFG", base)
    path = service._verifier_config(
        {
            "profile": {
                "languages": ["typescript"],
                "test_roots": ["tests"],
                "source_roots": ["src"],
            }
        },
        tmp_path / "verdict.json",
    )
    generated = path.read_text()
    assert "test_framework: node" in generated
    assert "--test-reporter=junit" in generated
