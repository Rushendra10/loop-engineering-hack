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

    def fake_agent(target, issue, issue_text, reason_codes=None, deadline_s=900, model="auto"):
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
    closed = []
    monkeypatch.setattr(service, "close_issue", lambda *args: closed.append(args))

    service.JOBS["abc"] = {
        "status": "running",
        "repo": "https://github.com/acme/widget",
        "issue": 7,
        "verdict": None,
        "pr_url": None,
        "issue_closed": False,
        "branch": None,
        "attempt": 1,
        "stage": "queued",
        "events": [],
        "settings": {
            "model": "auto",
            "deadline_s": 900,
            "retry_on_rejection": True,
            "close_issue": True,
        },
    }
    service.run_job("abc")

    assert service.JOBS["abc"]["status"] == "done"
    assert service.JOBS["abc"]["stage"] == "finished"
    assert service.JOBS["abc"]["branch"] == "fixloop/issue-7"
    assert service.JOBS["abc"]["pr_url"].endswith("/pull/1")
    assert service.JOBS["abc"]["issue_closed"] is True
    assert closed[0][2:] == (7, "https://github.com/acme/widget/pull/1")
    events = service.JOBS["abc"]["events"]
    messages = [event["message"] for event in events]
    assert any("Akash lease attached" in message for message in messages)
    assert any("Run complete" in message for message in messages)
    assert all("source" in event for event in events)
    assert any(event["source"] == "akash" for event in events)
    assert any(event["source"] == "github" for event in events)
    assert service.JOBS["abc"]["profile"]["source_roots"] == ["src"]


def test_system_status_contains_no_secrets():
    status = service.system_status()
    assert status["runtime"]
    assert status["verifier"] in {"local", "buildkite"}
    assert "commit_contract" in status
    assert set(status["infrastructure"]) == {"akash", "x402", "buildkite"}
    assert status["infrastructure"]["akash"]["status"] == "connected"
    assert status["infrastructure"]["buildkite"]["pipeline"]
    assert all("token" not in key.casefold() and "secret" not in key.casefold() for key in status)


def test_open_pr_requires_token(monkeypatch, tmp_path):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GH_TOKEN"):
        service.open_pr(tmp_path, "https://github.com/acme/widget", "fixloop/issue-1", "main", {})


def test_close_issue_marks_github_issue_completed(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(service, "_run", lambda command, cwd=None, timeout=120: calls.append((command, cwd)))
    service.close_issue(
        tmp_path,
        "https://github.com/acme/widget",
        42,
        "https://github.com/acme/widget/pull/9",
    )
    command, cwd = calls[0]
    assert command[:4] == ["gh", "issue", "close", "42"]
    assert command[command.index("--repo") + 1] == "acme/widget"
    assert "pull/9" in command[command.index("--comment") + 1]
    assert cwd == tmp_path


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
