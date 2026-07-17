from pathlib import Path

import runner


NODE_CONFIG = {
    "test_framework": "node",
    "node_test_cmd": ["node", "--test", "--test-reporter=junit"],
    "timeout_s": 30,
}


def write_test(root: Path, name: str, assertion: str) -> str:
    path = root / name
    path.write_text(
        'import test from "node:test";\n'
        'import assert from "node:assert/strict";\n'
        f'test("behavior", () => {{ {assertion} }});\n'
    )
    return name


def test_node_assertion_failure_is_a_real_regression(tmp_path):
    target = write_test(tmp_path, "regression.test.mjs", "assert.equal(1, 2);")
    result = runner.expect_fail(tmp_path, [target], NODE_CONFIG)
    assert result["ok"] is True
    assert set(result["results"].values()) == {"failed"}


def test_node_passing_test_passes_twice(tmp_path):
    target = write_test(tmp_path, "regression.test.mjs", "assert.equal(2, 2);")
    result = runner.expect_pass_twice(tmp_path, [target], NODE_CONFIG)
    assert result["ok"] is True
    assert len(result["runs"]) == 2


def test_node_syntax_error_is_not_accepted_as_regression(tmp_path):
    path = tmp_path / "broken.test.mjs"
    path.write_text('import test from "node:test";\ntest("broken", () => {\n')
    result = runner.expect_fail(tmp_path, [path.name], NODE_CONFIG)
    assert result["ok"] is False
    assert result["code"] == "NEW_TEST_HARNESS_ERROR"
