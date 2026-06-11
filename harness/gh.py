"""GitHub adapter: every gh/git subprocess in one place.

Owns the platform edge cases: secondary-rate-limit backoff, ≥30s spacing on
content-creating calls, async fork readiness, fork-name collisions, live
default-branch fetch, CI rollup classification, repos keyed tolerant of rename.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .policy import RepoFacts

WRITE_SPACING_S = 30
_last_write = 0.0


class GhError(Exception):
    pass


def _run(args: list[str], *, timeout: int = 120, cwd: Path | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, env=env)


def gh(args: list[str], *, timeout: int = 120, retries: int = 2) -> str:
    """Run gh; transparently absorb secondary-rate-limit pushback."""
    for attempt in range(retries + 1):
        r = _run(["gh"] + args, timeout=timeout)
        if r.returncode == 0:
            return r.stdout
        err = (r.stderr or r.stdout or "").strip()
        if "secondary rate limit" in err.lower() or "abuse" in err.lower():
            if attempt < retries:
                time.sleep(60 + attempt * 30)
                continue
        raise GhError(err[:500] or f"gh {' '.join(args[:3])} failed")
    raise GhError("unreachable")


def gh_json(args: list[str], *, timeout: int = 120):
    return json.loads(gh(args, timeout=timeout) or "null")


def paced_write(fn, *args, **kwargs):
    """Enforce ≥30s between content-creating calls (anti-abuse, §8)."""
    global _last_write
    wait = WRITE_SPACING_S - (time.monotonic() - _last_write)
    if wait > 0:
        time.sleep(wait)
    try:
        return fn(*args, **kwargs)
    finally:
        _last_write = time.monotonic()


# ── repo profile / preflight facts ─────────────────────────────────────────

_AI_FORBIDDEN_RE = re.compile(
    r"(no ai|not accept(ing)? ai|ai[- ]generated (code|contributions?) (is|are) not|"
    r"reject ai|llm[- ]generated.{0,40}not (welcome|accepted))", re.IGNORECASE)
_AI_DISCLOSE_RE = re.compile(r"(disclose|declare).{0,60}(ai|llm)", re.IGNORECASE)


def repo_facts(repo: str) -> RepoFacts:
    """Live fetch — never cache default_branch across runs (deec038 lesson).
    Schema-tolerant: missing/deprecated fields degrade, never crash."""
    raw = gh_json(["api", f"repos/{repo}"])
    license_spdx = ((raw.get("license") or {}).get("spdx_id")) or ""
    ai_policy = "unknown"
    try:
        c = gh(["api", f"repos/{repo}/contents/CONTRIBUTING.md",
                "-H", "Accept: application/vnd.github.raw"], timeout=60)
        if _AI_FORBIDDEN_RE.search(c):
            ai_policy = "forbidden"
        elif _AI_DISCLOSE_RE.search(c):
            ai_policy = "disclosed_only"
    except (GhError, subprocess.TimeoutExpired):
        pass
    return RepoFacts(
        repo=raw.get("full_name", repo),  # follows renames
        archived=bool(raw.get("archived")),
        disabled=bool(raw.get("disabled")),
        license_spdx=license_spdx,
        ai_policy=ai_policy,
        pushed_at=raw.get("pushed_at") or "",
        default_branch=raw.get("default_branch") or "main",
        size_kb=int(raw.get("size") or 0),
    )


# ── fork lifecycle ─────────────────────────────────────────────────────────

def ensure_fork(repo: str, login: str, sleeper=time.sleep) -> str:
    """Fork (no clone) and wait until git-reachable. Resolves name collisions
    by matching parent.full_name, never by guessing names."""
    try:
        gh(["repo", "fork", repo, "--clone=false"], timeout=120)
    except GhError as e:
        if "already exists" not in str(e).lower():
            raise
    fork = _find_fork(repo, login)
    for delay in (2, 4, 8, 16, 32, 60, 60):
        r = _run(["git", "ls-remote", f"https://github.com/{fork}.git", "HEAD"], timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return fork
        sleeper(delay)
    raise GhError(f"fork {fork} never became reachable")


def _find_fork(repo: str, login: str) -> str:
    owner_repo = repo.split("/")[1]
    try:
        mine = gh_json(["api", f"repos/{login}/{owner_repo}"])
        if (mine.get("parent") or {}).get("full_name", "").lower() == repo.lower():
            return mine["full_name"]
    except GhError:
        pass
    forks = gh_json(["api", f"repos/{repo}/forks", "--paginate", "-q",
                     f'[.[] | select(.owner.login == "{login}") | .full_name]'])
    if forks:
        return forks[0]
    return f"{login}/{owner_repo}"  # freshly created, API not consistent yet


def clone(fork: str, upstream: str, workdir: Path, *, default_branch: str,
          size_kb: int) -> Path:
    """Blobless partial clone over 200MB; branch from upstream, never trust fork state."""
    dest = workdir / fork.split("/")[1]
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN", "")
    url = f"https://x-access-token:{token}@github.com/{fork}.git" if token \
        else f"https://github.com/{fork}.git"
    args = ["git", "clone", "--filter=blob:none"] if size_kb > 200_000 else ["git", "clone"]
    r = _run(args + [url, str(dest)], timeout=600)
    if r.returncode != 0:
        raise GhError(_mask(r.stderr)[:300])
    for cmd in (["git", "remote", "add", "upstream", f"https://github.com/{upstream}.git"],
                ["git", "fetch", "upstream", default_branch, "--depth", "200"]):
        r = _run(cmd, cwd=dest, timeout=300)
        if r.returncode != 0:
            raise GhError(_mask(r.stderr)[:300])
    return dest


def create_branch(repo_path: Path, branch: str, default_branch: str) -> str:
    r = _run(["git", "checkout", "-b", branch, f"upstream/{default_branch}"],
             cwd=repo_path, timeout=60)
    if r.returncode != 0:
        raise GhError(_mask(r.stderr)[:300])
    sha = _run(["git", "rev-parse", "HEAD"], cwd=repo_path, timeout=30).stdout.strip()
    return sha


def commit_and_push(repo_path: Path, branch: str, message: str, *,
                    login: str, email: str, signoff: bool) -> None:
    for cmd in (["git", "config", "user.name", login],
                ["git", "config", "user.email", email],
                ["git", "add", "-A"]):
        _run(cmd, cwd=repo_path, timeout=30)
    commit = ["git", "commit", "-m", message] + (["-s"] if signoff else [])
    r = _run(commit, cwd=repo_path, timeout=60)
    if r.returncode != 0:
        raise GhError(f"commit failed: {_mask(r.stderr)[:300]}")
    r = _run(["git", "push", "origin", branch], cwd=repo_path, timeout=300)
    if r.returncode != 0:
        raise GhError(f"push failed: {_mask(r.stderr)[:300]}")


def _mask(text: str) -> str:
    return re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", text or "")


# ── queries the apps need ──────────────────────────────────────────────────

def our_existing_pr(upstream: str, login: str, issue_number: int) -> int:
    """Adopt-don't-duplicate: an open PR of ours for this issue (crashed run)."""
    prs = gh_json(["api", f"repos/{upstream}/pulls?state=open&per_page=100",
                   "-q", f'[.[] | select(.user.login == "{login}") '
                         f'| {{number, body, head: .head.ref}}]'])
    for pr in prs or []:
        if f"#{issue_number}" in (pr.get("body") or "") or \
                pr.get("head", "").endswith(f"-{issue_number}") or \
                f"issue-{issue_number}-" in pr.get("head", ""):
            return pr["number"]
    return 0


def issue_live(repo: str, number: int) -> dict:
    return gh_json(["api", f"repos/{repo}/issues/{number}"])


def list_my_open_prs(login: str, limit: int) -> list[dict]:
    return gh_json(["search", "prs", "--author", login, "--state", "open",
                    "--limit", str(limit), "--json",
                    "repository,number,title,updatedAt,isDraft"]) or []


def pr_view(repo: str, number: int) -> dict:
    return gh_json(["pr", "view", str(number), "-R", repo, "--json",
                    "state,mergeable,statusCheckRollup,reviews,comments,labels,"
                    "isDraft,baseRefName,headRefName,author,closed,mergedAt"])


def classify_ci(rollup: list[dict] | None) -> str:
    """pending_approval | running | passed | failed | no_ci (§5: pending ≠ failure)."""
    if not rollup:
        return "no_ci"
    states = {(c.get("conclusion") or c.get("state") or "").upper() for c in rollup}
    if "FAILURE" in states or "ERROR" in states:
        return "failed"
    if "ACTION_REQUIRED" in states or "WAITING" in states:
        return "pending_approval"
    if "PENDING" in states or "IN_PROGRESS" in states or "QUEUED" in states or "" in states:
        return "running"
    return "passed"


def rate_remaining() -> float:
    """Fraction of core REST quota remaining; <0.15 → end batch early (§10)."""
    try:
        core = gh_json(["api", "rate_limit"])["resources"]["core"]
        return core["remaining"] / max(1, core["limit"])
    except (GhError, KeyError, TypeError, json.JSONDecodeError):
        return 1.0
