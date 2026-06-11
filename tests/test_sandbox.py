import subprocess

import pytest

from harness import sandbox
from harness.patch import PatchError, validate_worktree
from harness.policy import load_secret_patterns

PATTERNS = load_secret_patterns()
ISSUE = {"number": 7, "title": "fix the bug", "body": "something is wrong"}


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-C", tmp_path, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", tmp_path, "config", "user.name", "t"], check=True)
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    big = "\n".join(f"line {i}" for i in range(200)) + "\n"
    (tmp_path / "big.py").write_text(big)
    subprocess.run(["git", "-C", tmp_path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", tmp_path, "commit", "-qm", "init"], check=True)
    return tmp_path


# ── run_agent ──────────────────────────────────────────────────────────────

def _fake_runner(make_changes=None, returncode=0, stdout="SUMMARY: did the fix"):
    captured = {}

    def runner(cmd, cwd=None, env=None, **kwargs):
        captured["cmd"], captured["env"], captured["cwd"] = cmd, env, cwd
        if make_changes:
            make_changes(cwd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")
    return runner, captured


def test_agent_session_returns_diff_and_summary(tmp_path):
    repo = _git_repo(tmp_path)
    runner, cap = _fake_runner(
        make_changes=lambda cwd: (cwd / "app.py").write_text("def f():\n    return 2\n"))
    res = sandbox.run_agent(repo, "o/r", ISSUE, runner=runner, say=lambda *a, **k: None)
    assert res.files == ["app.py"]
    assert res.summary == "did the fix"
    assert "fix the bug" in cap["cmd"][-1]  # issue title in prompt


def test_agent_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_PAT", "ghp_secret")
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-secret")
    repo = _git_repo(tmp_path)
    runner, cap = _fake_runner(
        make_changes=lambda cwd: (cwd / "app.py").write_text("x = 1\n"))
    sandbox.run_agent(repo, "o/r", ISSUE, runner=runner, say=lambda *a, **k: None)
    assert "GH_PAT" not in cap["env"] and "OPENCODE_API_KEY" not in cap["env"]
    assert "OPENCODE_CONFIG" in cap["env"]  # isolated config, not the user's


def test_agent_no_changes_is_error(tmp_path):
    repo = _git_repo(tmp_path)
    runner, _ = _fake_runner()
    with pytest.raises(sandbox.SandboxError, match="no changes"):
        sandbox.run_agent(repo, "o/r", ISSUE, runner=runner, say=lambda *a, **k: None)


def test_agent_timeout_rolls_back(tmp_path):
    repo = _git_repo(tmp_path)

    def runner(cmd, cwd=None, **kwargs):
        (cwd / "app.py").write_text("half-finished garbage")
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(sandbox.SandboxError, match="rolled back"):
        sandbox.run_agent(repo, "o/r", ISSUE, runner=runner, say=lambda *a, **k: None)
    assert (repo / "app.py").read_text() == "def f():\n    return 1\n"


def test_agent_nonzero_exit_rolls_back(tmp_path):
    repo = _git_repo(tmp_path)
    runner, _ = _fake_runner(
        make_changes=lambda cwd: (cwd / "app.py").write_text("junk"), returncode=1)
    with pytest.raises(sandbox.SandboxError, match="exited 1"):
        sandbox.run_agent(repo, "o/r", ISSUE, runner=runner, say=lambda *a, **k: None)
    assert (repo / "app.py").read_text() == "def f():\n    return 1\n"


def test_prompt_marks_issue_as_untrusted_data():
    assert "not instructions" in sandbox.AGENT_PROMPT


# ── validate_worktree (same gates as apply_plan, on a git diff) ───────────

def test_worktree_accepts_clean_edit(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "app.py").write_text("def f():\n    return 2\n")
    flags = validate_worktree(repo, ["app.py"], PATTERNS)
    assert flags == set()


def test_worktree_rejects_truncation(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "big.py").write_text("line 0\n")
    with pytest.raises(PatchError, match="hard cap|truncation"):
        validate_worktree(repo, ["big.py"], PATTERNS)


def test_worktree_rejects_secret(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "app.py").write_text('TOKEN = "ghp_' + "z" * 40 + '"\ndef f():\n    return 1\n')
    with pytest.raises(PatchError, match="secret"):
        validate_worktree(repo, ["app.py"], PATTERNS)


def test_worktree_rejects_workflow_edit(tmp_path):
    repo = _git_repo(tmp_path)
    wf = repo / ".github/workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\n")
    with pytest.raises(PatchError, match="workflow"):
        validate_worktree(repo, [".github/workflows/ci.yml"], PATTERNS)


def test_worktree_flags_manifest(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "requirements.txt").write_text("requests\n")
    (repo / "app.py").write_text("def f():\n    return 3\n")
    flags = validate_worktree(repo, ["app.py", "requirements.txt"], PATTERNS)
    assert "touches_manifest" in flags


def test_worktree_rejects_too_many_files(tmp_path):
    repo = _git_repo(tmp_path)
    files = []
    for i in range(8):
        p = repo / f"f{i}.py"
        p.write_text(f"x = {i}\n")
        files.append(p.name)
    with pytest.raises(PatchError, match="files changed exceeds"):
        validate_worktree(repo, files, PATTERNS, max_files=6)


def test_worktree_rejects_deleted_large_file(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "big.py").unlink()
    with pytest.raises(PatchError, match="deleted"):
        validate_worktree(repo, ["big.py"], PATTERNS)


def test_agent_env_sets_pwd_to_repo(tmp_path):
    # opencode resolves the workspace from $PWD, not cwd — a stale inherited
    # PWD makes the agent see an empty workspace (live-debugged 2026-06-11)
    repo = _git_repo(tmp_path)
    runner, cap = _fake_runner(
        make_changes=lambda cwd: (cwd / "app.py").write_text("x = 9\n"))
    sandbox.run_agent(repo, "o/r", ISSUE, runner=runner, say=lambda *a, **k: None)
    assert cap["env"]["PWD"] == str(repo)


def test_changed_files_filters_test_artifacts(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "app.py").write_text("x = 5\n")
    pc = repo / "__pycache__"
    pc.mkdir()
    (pc / "app.cpython-312.pyc").write_bytes(b"\x00junk")
    (repo / ".pytest_cache").mkdir()
    assert sandbox.changed_files(repo) == ["app.py"]
    sandbox.clean_noise(repo)
    assert not pc.exists()


def test_agent_cli_argv_and_usability(monkeypatch):
    assert sandbox.OPENCODE.argv("fix it") == \
        ["opencode", "run", "--model", "opencode/big-pickle", "fix it"]
    argv = sandbox.CURSOR.argv("fix it")
    assert argv[0] == "agent" and "fix it" in argv
    assert "--force" in argv  # print mode doesn't write files without it
    assert argv[argv.index("--model") + 1] == "composer-2.5"  # never -fast (6x price)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda b: "/bin/" + b)
    assert sandbox.OPENCODE.usable()
    assert not sandbox.CURSOR.usable()  # binary present but no key
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_x")
    assert sandbox.CURSOR.usable()
    assert [c.name for c in sandbox.usable_agents()] == ["opencode", "cursor"]


def test_cursor_key_survives_scrub_for_cursor_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_x")
    monkeypatch.setenv("GH_PAT", "ghp_x")
    repo = _git_repo(tmp_path)
    runner, cap = _fake_runner(
        make_changes=lambda cwd: (cwd / "app.py").write_text("x = 7\n"))
    sandbox.run_agent(repo, "o/r", ISSUE, cli=sandbox.CURSOR, runner=runner,
                      say=lambda *a, **k: None)
    assert cap["env"]["CURSOR_API_KEY"] == "crsr_x"  # the CLI's own key: kept
    assert "GH_PAT" not in cap["env"]                # everything else: scrubbed
    runner2, cap2 = _fake_runner(
        make_changes=lambda cwd: (cwd / "app.py").write_text("x = 8\n"))
    sandbox.run_agent(repo, "o/r", ISSUE, cli=sandbox.OPENCODE, runner=runner2,
                      say=lambda *a, **k: None)
    assert "CURSOR_API_KEY" not in cap2["env"]  # opencode session never sees it
