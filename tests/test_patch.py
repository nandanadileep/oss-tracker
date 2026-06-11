import pytest

from harness.patch import (Edit, PatchError, PatchPlan, apply_plan,
                           deletion_guardrail, locate, parse_output,
                           validate_path)
from harness.policy import load_secret_patterns

PATTERNS = load_secret_patterns()


# ── parsing ────────────────────────────────────────────────────────────────

SR_OUTPUT = """SUMMARY: fix off-by-one
COMMIT: fix: off-by-one in pager

PATH: src/pager.py
<<<<<<< SEARCH
def page(n):
    return n + 2
=======
def page(n):
    return n + 1
>>>>>>> REPLACE
"""

FULL_OUTPUT = """SUMMARY: new helper
PATH: src/util.py
```
def helper():
    return 42
```
"""


def test_parse_search_replace():
    plan = parse_output(SR_OUTPUT)
    assert plan.summary == "fix off-by-one"
    assert plan.commit_message == "fix: off-by-one in pager"
    assert plan.edits[0].kind == "search_replace"
    assert "n + 2" in plan.edits[0].search


def test_parse_full_file():
    plan = parse_output(FULL_OUTPUT)
    assert plan.edits[0].kind == "full_file"
    assert "return 42" in plan.edits[0].content


def test_parse_prose_raises():
    with pytest.raises(PatchError, match="no PATH blocks"):
        parse_output("I think the fix is to change the loop variable.")


# ── path validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "../etc/passwd", "/abs/path", "node_modules/x.js", "vendor/lib.py",
    ".github/workflows/ci.yml", "package-lock.json", "app.min.js",
])
def test_forbidden_paths(tmp_path, bad):
    with pytest.raises(PatchError):
        validate_path(tmp_path, bad)


def test_binary_and_symlink_rejected(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"abc\x00def")
    with pytest.raises(PatchError, match="binary"):
        validate_path(tmp_path, "blob.bin")
    (tmp_path / "real.py").write_text("x = 1\n")
    (tmp_path / "link.py").symlink_to(tmp_path / "real.py")
    with pytest.raises(PatchError, match="symlink"):
        validate_path(tmp_path, "link.py")


# ── fuzzy locate ───────────────────────────────────────────────────────────

def test_locate_exact():
    hay = "alpha\nbeta\ngamma\n"
    assert hay[slice(*locate(hay, "beta"))] == "beta"


def test_locate_fuzzy_whitespace_drift():
    hay = "def f(x):\n    return  x + 1\n\nprint(f(2))\n"
    start, end = locate(hay, "def f(x):\n    return x + 1")
    assert "return" in hay[start:end]


def test_locate_multiple_exact_is_ambiguous():
    with pytest.raises(PatchError, match="multiple"):
        locate("dup\nother\ndup\n", "dup")


def test_locate_no_match():
    with pytest.raises(PatchError, match="not found"):
        locate("aaa\nbbb\n", "completely absent block of text")


# ── guardrails ─────────────────────────────────────────────────────────────

def test_deletion_hard_cap():
    old = "\n".join(f"line {i}" for i in range(400))
    with pytest.raises(PatchError, match="hard cap"):
        deletion_guardrail("f.py", old, "line 0")


def test_deletion_soft_cap_requires_intent():
    old = "\n".join(f"line {i}" for i in range(240))
    new = "\n".join(f"line {i}" for i in range(120))
    with pytest.raises(PatchError, match="DELETION INTENT"):
        deletion_guardrail("f.py", old, new)
    deletion_guardrail("f.py", old, new + "\n# DELETION INTENT: dead code removal")


def test_truncation_artifact_detected():
    old = "\n".join(f"line {i}" for i in range(150))
    new = "\n".join(f"line {i}" for i in range(60))
    with pytest.raises(PatchError, match="truncation"):
        deletion_guardrail("f.py", old, new)


# ── apply (transactional) ──────────────────────────────────────────────────

def _repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("def f():\n    return 1\n")
    return tmp_path


def test_apply_search_replace(tmp_path):
    repo = _repo(tmp_path)
    plan = PatchPlan(edits=[Edit("src/a.py", "search_replace",
                                 search="    return 1", replace="    return 2")])
    changed = apply_plan(repo, plan, PATTERNS)
    assert changed == ["src/a.py"]
    assert "return 2" in (repo / "src/a.py").read_text()


def test_apply_preserves_crlf(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src/w.py").write_text("a = 1\r\nb = 2\r\n", newline="")
    plan = PatchPlan(edits=[Edit("src/w.py", "search_replace", search="a = 1", replace="a = 9")])
    apply_plan(repo, plan, PATTERNS)
    assert (repo / "src/w.py").read_bytes() == b"a = 9\r\nb = 2\r\n"


def test_apply_rejects_secret_introduction(tmp_path):
    repo = _repo(tmp_path)
    plan = PatchPlan(edits=[Edit("src/a.py", "search_replace", search="    return 1",
                                 replace='    return "ghp_' + "c" * 40 + '"')])
    with pytest.raises(PatchError, match="secret"):
        apply_plan(repo, plan, PATTERNS)


def test_apply_size_caps(tmp_path):
    repo = _repo(tmp_path)
    big = "\n".join(f"x{i} = {i}" for i in range(400)) + "\n"
    plan = PatchPlan(edits=[Edit("src/a.py", "full_file", content=big)])
    with pytest.raises(PatchError, match="lines changed exceeds"):
        apply_plan(repo, plan, PATTERNS, max_lines=300)


def test_apply_flags_manifest(tmp_path):
    repo = _repo(tmp_path)
    (repo / "requirements.txt").write_text("requests==2.0\n")
    plan = PatchPlan(edits=[Edit("requirements.txt", "full_file", content="requests==2.1\n")])
    apply_plan(repo, plan, PATTERNS)
    assert "touches_manifest" in plan.flags


def test_apply_missing_file_raises(tmp_path):
    repo = _repo(tmp_path)
    plan = PatchPlan(edits=[Edit("src/ghost.py", "full_file", content="x = 1\n")])
    with pytest.raises(PatchError, match="does not exist"):
        apply_plan(repo, plan, PATTERNS)
