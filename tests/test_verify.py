import subprocess

import pytest

from harness.verify import (VerifyResult, delta, detect_commands, run_tests,
                            scrubbed_env)


def test_scrubbed_env_strips_secrets_but_keeps_path(monkeypatch):
    monkeypatch.setenv("GH_PAT", "ghp_xyz")
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-1")
    monkeypatch.setenv("MY_PASSWORD", "hunter2")
    monkeypatch.setenv("PAT", "bare")
    monkeypatch.setenv("HOME", "/home/x")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = scrubbed_env()
    assert "GH_PAT" not in env and "OPENCODE_API_KEY" not in env
    assert "MY_PASSWORD" not in env and "PAT" not in env
    assert env["PATH"] == "/usr/bin"  # the PAT pattern must never eat PATH
    assert env["HOME"] == "/home/x" and env["CI"] == "true"


def test_detect_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    cmds = detect_commands(tmp_path)
    assert cmds and "pytest" in " ".join(cmds[0])


def test_detect_bun_over_npm(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    (tmp_path / "bun.lock").write_text("")
    assert detect_commands(tmp_path)[0][0] == "bun"


def test_detect_nothing(tmp_path):
    assert detect_commands(tmp_path) == []
    assert run_tests(tmp_path).outcome == "no_tests"


def _runner(returncode=0, stdout="", stderr=""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    return run


def test_infra_failure_distinguished_from_test_failure(tmp_path):
    r = run_tests(tmp_path, [["pytest"]],
                  runner=_runner(1, stderr="ModuleNotFoundError: No module named 'foo'"))
    assert r.outcome == "infra_failure"
    r = run_tests(tmp_path, [["pytest"]],
                  runner=_runner(1, stdout="FAILED tests/test_a.py::test_x - assert 1 == 2"))
    assert r.outcome == "failed"
    assert "tests/test_a.py::test_x" in next(iter(r.failing))


def test_missing_runner_is_infra(tmp_path):
    def boom(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])
    assert run_tests(tmp_path, [["bun", "test"]], runner=boom).outcome == "infra_failure"


def test_timeout_outcome(tmp_path):
    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
    assert run_tests(tmp_path, [["make", "test"]], runner=slow).outcome == "timeout"


# ── baseline delta (§5: only NEW failures are the patch's fault) ──────────

def test_delta_preexisting_failures_pass():
    base = VerifyResult("failed", failing=frozenset({"t::a", "t::b"}))
    after = VerifyResult("failed", failing=frozenset({"t::a"}))
    assert delta(base, after).outcome == "passed"


def test_delta_new_failure_blamed():
    base = VerifyResult("failed", failing=frozenset({"t::a"}))
    after = VerifyResult("failed", failing=frozenset({"t::a", "t::new"}))
    d = delta(base, after)
    assert d.outcome == "failed" and "t::new" in d.detail


def test_delta_clean_baseline_failure_stays_failed():
    assert delta(VerifyResult("passed"),
                 VerifyResult("failed", failing=frozenset({"t::x"}))).outcome == "failed"


def test_delta_pass_through_non_failures():
    assert delta(VerifyResult("passed"), VerifyResult("no_tests")).outcome == "no_tests"
