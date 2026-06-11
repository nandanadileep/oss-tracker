"""Patch validation and application — both engines funnel through here.

Two entry points:
  - apply_plan():        the one-shot fallback engine — parse model text,
                         fuzzy-locate, apply transactionally.
  - validate_worktree(): the sandbox engine — an agent already edited the
                         repo; validate its git diff with the SAME gates.

Owns every patch edge case in DOMAIN_MODEL.md §5: forbidden paths, traversal,
binary files, EOL preservation, ambiguous matches, deletion guardrails,
truncation artefacts, secret-introducing diffs, full rollback on any failure.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from .policy import scan_secrets

FORBIDDEN_PARTS = {"node_modules", "vendor", "dist", "build", ".git"}
FORBIDDEN_SUFFIXES = {".lock", ".min.js", ".min.css", ".map"}
FORBIDDEN_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
                   "poetry.lock", "uv.lock", "Gemfile.lock", "composer.lock"}
# editing CI from a fork is ignored by GitHub and looks malicious
FORBIDDEN_PREFIXES = (".github/workflows",)
# new deps are a supply-chain decision → escalate, never auto-merge
MANIFEST_NAMES = {"package.json", "requirements.txt", "pyproject.toml", "Cargo.toml",
                  "go.mod", "Gemfile", "setup.py", "build.gradle", "pom.xml"}

HARD_DELETE_CAP = 150
SOFT_DELETE_CAP = 100
TRUNCATION_MIN_LINES = 100
TRUNCATION_SHRINK_RATIO = 0.5
FUZZY_THRESHOLD = 0.8
AMBIGUITY_MARGIN = 0.05


class PatchError(Exception):
    """Raised with a machine-usable reason; fed back to the model on retry."""


@dataclass(frozen=True)
class Edit:
    path: str
    kind: str  # "search_replace" | "full_file"
    search: str = ""
    replace: str = ""
    content: str = ""


@dataclass
class PatchPlan:
    edits: list[Edit] = field(default_factory=list)
    summary: str = ""
    commit_message: str = ""
    test_commands: list[list[str]] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)  # e.g. {"touches_manifest"}


# ── parsing ────────────────────────────────────────────────────────────────

_PATH_RE = re.compile(r"^PATH:\s*(\S+)\s*$", re.MULTILINE)
_SR_RE = re.compile(
    r"<{7} SEARCH\n(?P<search>.*?)\n={7}\n(?P<replace>.*?)\n>{7} REPLACE",
    re.DOTALL,
)


def parse_output(text: str) -> PatchPlan:
    """Parse the model's PATH-block contract: SEARCH/REPLACE or fenced full file."""
    plan = PatchPlan()
    matches = list(_PATH_RE.finditer(text))
    if not matches:
        raise PatchError("no PATH blocks found in model output")
    for i, m in enumerate(matches):
        path = m.group(1).strip()
        chunk = text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        srs = list(_SR_RE.finditer(chunk))
        if srs:
            for sr in srs:
                plan.edits.append(Edit(path, "search_replace",
                                       search=sr.group("search"), replace=sr.group("replace")))
            continue
        fence = re.search(r"```[a-zA-Z0-9]*\n(.*?)```", chunk, re.DOTALL)
        if fence:
            plan.edits.append(Edit(path, "full_file", content=fence.group(1)))
            continue
        raise PatchError(f"PATH block for {path} has neither SEARCH/REPLACE nor a fenced file")
    plan.summary = _first_line_after(text, "SUMMARY:")
    plan.commit_message = _first_line_after(text, "COMMIT:") or plan.summary
    return plan


def _first_line_after(text: str, marker: str) -> str:
    m = re.search(rf"^{re.escape(marker)}\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


# ── path validation ────────────────────────────────────────────────────────

def validate_path(repo_root: Path, rel_path: str) -> Path:
    if rel_path.startswith(("/", "~")) or ".." in Path(rel_path).parts:
        raise PatchError(f"unsafe path: {rel_path}")
    if any(part in FORBIDDEN_PARTS for part in Path(rel_path).parts):
        raise PatchError(f"forbidden directory: {rel_path}")
    if rel_path.startswith(FORBIDDEN_PREFIXES):
        raise PatchError(f"workflow files may not be edited from a fork: {rel_path}")
    name = Path(rel_path).name
    if name in FORBIDDEN_NAMES or any(name.endswith(s) for s in FORBIDDEN_SUFFIXES):
        raise PatchError(f"generated/lock file may not be edited: {rel_path}")
    target = (repo_root / rel_path)
    resolved = target.resolve()
    if not str(resolved).startswith(str(repo_root.resolve())):
        raise PatchError(f"path escapes repo: {rel_path}")
    if target.is_symlink():
        raise PatchError(f"symlink may not be edited: {rel_path}")
    if target.exists() and b"\x00" in target.read_bytes()[:8192]:
        raise PatchError(f"binary file may not be edited: {rel_path}")
    return target


# ── fuzzy matching ─────────────────────────────────────────────────────────

def locate(haystack: str, needle: str) -> tuple[int, int]:
    """Find needle in haystack: exact first, then fuzzy line-window match.

    Returns (start, end) char offsets. Raises on no-match or ambiguity —
    we never guess between two plausible sites (DOMAIN_MODEL.md §5).
    """
    count = haystack.count(needle)
    if count == 1:
        i = haystack.index(needle)
        return i, i + len(needle)
    if count > 1:
        raise PatchError("search block matches multiple locations; need more context lines")

    hay_lines = haystack.splitlines(keepends=True)
    needle_norm = needle.strip()
    n = max(1, len(needle.splitlines()))
    scored: list[tuple[float, int]] = []  # (ratio, line index)
    for i in range(0, max(1, len(hay_lines) - n + 1)):
        window = "".join(hay_lines[i:i + n]).strip()
        r = SequenceMatcher(None, window, needle_norm).ratio()
        if r >= FUZZY_THRESHOLD:
            scored.append((r, i))
    if not scored:
        raise PatchError("search block not found (best fuzzy match below threshold)")
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUITY_MARGIN \
            and scored[0][1] != scored[1][1]:
        raise PatchError("search block is ambiguous between multiple fuzzy locations")
    i = scored[0][1]
    start = sum(len(l) for l in hay_lines[:i])
    end = start + sum(len(l) for l in hay_lines[i:i + n])
    return start, end


# ── guardrails ─────────────────────────────────────────────────────────────

def deletion_guardrail(path: str, old: str, new: str) -> None:
    old_n, new_n = len(old.splitlines()), len(new.splitlines())
    deleted = max(0, old_n - new_n)
    if deleted > HARD_DELETE_CAP:
        raise PatchError(f"{path}: {deleted} net lines deleted exceeds hard cap {HARD_DELETE_CAP}")
    if deleted >= SOFT_DELETE_CAP and "DELETION INTENT:" not in new:
        raise PatchError(f"{path}: {deleted} lines deleted without DELETION INTENT")
    if old_n >= TRUNCATION_MIN_LINES and new_n < old_n * TRUNCATION_SHRINK_RATIO:
        raise PatchError(f"{path}: file shrank {old_n}->{new_n} lines; truncation artefact")


def size_guardrail(plan: PatchPlan, changed: dict[str, tuple[str, str]],
                   max_files: int, max_lines: int) -> None:
    if len(changed) > max_files:
        raise PatchError(f"{len(changed)} files changed exceeds cap {max_files}")
    total = 0
    for old, new in changed.values():
        sm = SequenceMatcher(None, old.splitlines(), new.splitlines())
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                total += max(i2 - i1, j2 - j1)
    if total > max_lines:
        raise PatchError(f"{total} lines changed exceeds cap {max_lines}")


def _match_eol(original: str, text: str) -> str:
    """Preserve the file's dominant EOL convention and trailing newline."""
    if "\r\n" in original:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    if original.endswith(("\n", "\r\n")) and not text.endswith(("\n", "\r\n")):
        text += "\r\n" if "\r\n" in original else "\n"
    return text


# ── application (transactional) ────────────────────────────────────────────

def apply_plan(repo_root: Path, plan: PatchPlan, secret_patterns,
               *, max_files: int = 6, max_lines: int = 300) -> list[str]:
    """Apply all edits or none. Returns changed paths. Flags manifest edits."""
    repo_root = Path(repo_root)
    changed: dict[str, tuple[str, str]] = {}  # path -> (old, new)

    def _read(p: Path) -> str:
        # newline="" preserves CRLF so EOL matching can see it
        with p.open(encoding="utf-8", errors="replace", newline="") as f:
            return f.read()

    for edit in plan.edits:
        target = validate_path(repo_root, edit.path)
        if not target.exists():
            raise PatchError(f"file does not exist: {edit.path}")
        old = changed.get(edit.path, (None, None))[1] or _read(target)
        if edit.kind == "search_replace":
            search = edit.search.replace("\r\n", "\n")
            search = search.replace("\n", "\r\n") if "\r\n" in old else search
            start, end = locate(old, search)
            new = _match_eol(old, old[:start] + edit.replace + old[end:])
        else:
            new = _match_eol(old, edit.content)
        deletion_guardrail(edit.path, old, new)
        diff_added = "\n".join(set(new.splitlines()) - set(old.splitlines()))
        hits = scan_secrets(diff_added, secret_patterns)
        if hits:
            raise PatchError(f"{edit.path}: patch introduces secret-shaped content ({hits[0]})")
        original = changed.get(edit.path, (None, None))[0]
        changed[edit.path] = (original if original is not None else _read(target), new)

    if not changed:
        raise PatchError("plan contains no effective edits")
    size_guardrail(plan, changed, max_files, max_lines)
    if any(Path(p).name in MANIFEST_NAMES for p in changed):
        plan.flags.add("touches_manifest")

    # transactional write: keep originals, roll back everything on any failure
    def _write(p: Path, text: str) -> None:
        with p.open("w", encoding="utf-8", newline="") as f:  # write bytes verbatim
            f.write(text)

    written: list[str] = []
    try:
        for rel, (_, new) in changed.items():
            _write(repo_root / rel, new)
            written.append(rel)
    except OSError:
        for rel in written:
            _write(repo_root / rel, changed[rel][0])
        raise PatchError("write failed; rolled back")
    return sorted(changed)


# ── worktree validation (sandbox engine) ───────────────────────────────────

def _git_show_head(repo_path: Path, rel: str) -> str:
    r = subprocess.run(["git", "-C", str(repo_path), "show", f"HEAD:{rel}"],
                       capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else ""  # new file -> empty baseline


def validate_worktree(repo_path: Path, files: list[str], secret_patterns,
                      *, max_files: int = 6, max_lines: int = 300) -> set[str]:
    """Validate an agent-edited worktree with the same gates as apply_plan.

    Returns flags (e.g. {"touches_manifest"}). Raises PatchError on any
    violation — the CALLER rolls the worktree back (sandbox.rollback) so a
    rejected diff never reaches a commit.
    """
    repo_path = Path(repo_path)
    if not files:
        raise PatchError("worktree has no changes")
    flags: set[str] = set()
    changed: dict[str, tuple[str, str]] = {}

    for rel in files:
        target = validate_path(repo_path, rel)  # forbidden dirs/CI/locks/symlink/binary
        old = _git_show_head(repo_path, rel)
        if not target.exists():
            if old.splitlines() and len(old.splitlines()) > HARD_DELETE_CAP:
                raise PatchError(f"{rel}: agent deleted a {len(old.splitlines())}-line file")
            new = ""
        else:
            with target.open(encoding="utf-8", errors="replace", newline="") as f:
                new = f.read()
        deletion_guardrail(rel, old, new)
        added = "\n".join(set(new.splitlines()) - set(old.splitlines()))
        hits = scan_secrets(added, secret_patterns)
        if hits:
            raise PatchError(f"{rel}: diff introduces secret-shaped content ({hits[0]})")
        changed[rel] = (old, new)
        if Path(rel).name in MANIFEST_NAMES:
            flags.add("touches_manifest")

    size_guardrail(PatchPlan(), changed, max_files, max_lines)
    return flags
