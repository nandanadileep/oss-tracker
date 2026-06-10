#!/usr/bin/env python3
"""New Contributor engine.

Opens new high-quality OSS contributions from scored candidates.
Consumes candidates.json, produces PRs, and hands them to Backlog Steward.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".oss-harness" / "state"
CONFIG_FILE = ROOT / ".oss-harness" / "config.json"
CANDIDATES_FILE = STATE_DIR / "candidates.json"
QUEUE_FILE = STATE_DIR / "queue.json"
ACTIONS_FILE = STATE_DIR / "actions.jsonl"
PROGRESS_FILE = STATE_DIR / "progress.md"
PREFLIGHT = ROOT / ".oss-harness" / "scanners" / "repo-preflight.py"

OPENCODE_MODEL = "zen/big-pickle"
MAX_MODEL_ATTEMPTS = 3
MIN_CONFIDENCE = 5
MAX_DAILY_PRS = 5
MIN_DAILY_PRS = 1


@dataclass
class PatchEdit:
    path: str
    search: str
    replace: str


@dataclass
class PatchPlan:
    summary: str
    commit_message: str
    edits: list[PatchEdit]
    test_commands: list[list[str]]
    confidence: int


@dataclass
class ActionRecord:
    type: str
    target: str
    reason: str
    confidence: int
    created_at: str
    workflow_run_id: str | None = None
    comment_id: str | None = None
    pr_url: str | None = None
    commit_sha: str | None = None
    state: str | None = None
    dry_run: bool = False


@dataclass
class NewPRRecord:
    target: str
    pr_url: str
    repo: str
    issue_number: int
    commit_sha: str
    created_at: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def append_action(record: ActionRecord) -> None:
    ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ACTIONS_FILE.open("a") as f:
        f.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def gh_json(args: list[str], *, timeout: int = 120) -> Any:
    result = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh command failed")
    return json.loads(result.stdout or "null")


def gh_text(args: list[str], *, timeout: int = 120) -> str:
    result = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh command failed")
    return result.stdout


def run_preflight(repo: str) -> tuple[bool, str]:
    if not PREFLIGHT.exists():
        return True, "preflight script missing"
    result = subprocess.run(
        [sys.executable, str(PREFLIGHT), repo, "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 1:
        return False, result.stdout.strip() or result.stderr.strip() or "preflight blocked"
    return True, result.stdout.strip() or result.stderr.strip() or "preflight ok"


def run_opencode(prompt: str) -> str:
    result = subprocess.run(
        ["opencode", "run", "--format", "json", "--model", OPENCODE_MODEL, prompt],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "opencode failed")
    text_parts: list[str] = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            text = (event.get("part") or {}).get("text", "")
            if text:
                text_parts.append(text)
    return "\n".join(text_parts) if text_parts else result.stdout


def lint_comment(body: str, *, allow_close_word: bool = False) -> tuple[bool, str]:
    stripped = body.strip()
    if not stripped:
        return False, "empty comment"
    if len(stripped) > 600:
        return False, "comment exceeds 600 chars"
    if "\u2014" in stripped or "\u2013" in stripped:
        return False, "comment contains em/en dash"
    lowered = stripped.lower()
    banned_phrases = ("furthermore", "moreover", "in conclusion", "it's worth noting")
    for phrase in banned_phrases:
        if phrase in lowered:
            return False, f"comment contains banned phrase: {phrase}"
    if not allow_close_word and re.search(r"\bclose\b|\bclosing\b", lowered):
        return False, "comment mentions closing, which is disabled by policy"
    for line in stripped.splitlines():
        if re.match(r"^\s*([-*]|\d+[.)])\s+", line):
            return False, "comment contains bullet/list formatting"
    return True, "ok"


def load_candidates() -> dict[str, Any]:
    data = load_json(CANDIDATES_FILE, {"_version": "0.1.0", "candidates": []})
    if isinstance(data, list):
        data = {"_version": "0.1.0", "candidates": data}
    data.setdefault("candidates", [])
    return data


def select_candidates(data: dict[str, Any], max_count: int) -> list[dict[str, Any]]:
    """Select top-scored new issue candidates."""
    candidates = [
        c for c in data.get("candidates", [])
        if c.get("type") == "issue" and c.get("status") == "new"
    ]
    
    # Sort by total_score descending
    candidates.sort(key=lambda c: c.get("total_score", 0), reverse=True)
    
    # Filter out candidates that require deep domain knowledge unless score is high
    filtered = []
    for c in candidates:
        if c.get("requires_domain_knowledge", False) and c.get("total_score", 0) < 8:
            continue
        filtered.append(c)
    
    return filtered[:max_count]


def check_existing_prs(repo: str, issue_number: int, hari_login: str) -> bool:
    """Check if Hari already has a PR for this issue."""
    try:
        results = gh_json(
            ["search", "prs", f"repo:{repo} author:{hari_login} is:open", "--limit", "100", "--json", "title,body"]
        )
    except Exception:
        return False
    
    issue_ref = f"#{issue_number}"
    for pr in results or []:
        title = pr.get("title", "")
        body = pr.get("body", "")
        if issue_ref in title or issue_ref in body:
            return True
    return False


def fork_repo(repo: str) -> str:
    """Fork repo to Hari's account."""
    print(f"  forking {repo}")
    try:
        gh_text(["repo", "fork", repo, "--clone=false"])
    except Exception as exc:
        print(f"  fork may already exist: {exc}")
    return f"{load_json(CONFIG_FILE, {}).get('user', {}).get('login', 'Mr-Neutr0n')}/{repo.split('/', 1)[1]}"


def clone_repo(repo: str, workdir: Path, hari_login: str) -> Path:
    """Clone the fork."""
    fork = f"{hari_login}/{repo.split('/', 1)[1]}"
    token = os.environ.get("GITHUB_TOKEN", "")
    clone_url = f"https://x-access-token:{token}@github.com/{fork}.git"
    repo_path = workdir / repo.split("/", 1)[1]
    result = subprocess.run(["git", "clone", clone_url, str(repo_path)], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git clone failed")
    return repo_path


def create_branch(repo_path: Path, base_branch: str, issue_number: int) -> str:
    """Create a branch for the fix."""
    branch_name = f"fix-issue-{issue_number}"
    
    # Fetch upstream
    upstream_url = f"https://github.com/{repo_path.name}.git"
    run_cmd(["git", "remote", "add", "upstream", upstream_url], repo_path)
    run_cmd(["git", "fetch", "upstream", base_branch], repo_path, timeout=300)
    
    # Create branch from upstream base
    result = run_cmd(["git", "checkout", "-b", branch_name, f"upstream/{base_branch}"], repo_path)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git branch failed")
    
    return branch_name


def run_cmd(cmd: list[str], cwd: Path, *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def fetch_issue(repo: str, issue_number: int) -> dict[str, Any]:
    return gh_json(["issue", "view", str(issue_number), "-R", repo, "--json", "title,body,labels,comments"])


def collect_repo_context(repo_path: Path, max_chars: int = 50000) -> str:
    """Collect relevant repo files for context."""
    sections: list[str] = []
    used = 0
    
    # Prefer key files first
    key_files = ["README.md", "CONTRIBUTING.md", "setup.py", "pyproject.toml", "package.json", "go.mod", "Cargo.toml"]
    for key_file in key_files:
        path = repo_path / key_file
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                block = f"--- {key_file} ---\n{content[:5000]}"
                if used + len(block) > max_chars:
                    break
                sections.append(block)
                used += len(block)
            except Exception:
                continue
    
    return "\n\n".join(sections) if sections else "(no context available)"


def build_implementation_prompt(
    repo: str,
    issue_number: int,
    issue_data: dict[str, Any],
    repo_context: str,
) -> str:
    title = issue_data.get("title", "")
    body = (issue_data.get("body", "") or "")[:3000]
    return f"""Implement a minimal fix for this GitHub issue.

Rules:
- Return JSON only.
- Make the smallest targeted change.
- Use exact search/replace edits. The search text must appear exactly once.
- Do not remove unrelated code.
- Commit message must be professional and specific.
- Test commands should be commands worth running locally.

Repository: {repo}
Issue #{issue_number}: {title}

Issue body:
{body}

Repository context:
{repo_context}

Return only JSON:
{{
  "summary": "what changed",
  "commit_message": "commit message",
  "confidence": 1-10,
  "edits": [
    {{"path": "relative/path", "search": "exact text", "replace": "replacement text"}}
  ],
  "test_commands": [["pytest", "path/to/test.py"]]
}}
"""


def parse_patch_plan(output: str) -> PatchPlan:
    matches = re.findall(r"\{.*\}", output, re.DOTALL)
    for block in reversed(matches):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        edits = [
            PatchEdit(str(item.get("path", "")), str(item.get("search", "")), str(item.get("replace", "")))
            for item in data.get("edits", [])
            if isinstance(item, dict)
        ]
        tests = data.get("test_commands") or []
        test_commands = [cmd for cmd in tests if isinstance(cmd, list) and cmd and all(isinstance(part, str) for part in cmd)]
        return PatchPlan(
            summary=str(data.get("summary", "") or ""),
            commit_message=str(data.get("commit_message", "") or ""),
            edits=edits,
            test_commands=test_commands,
            confidence=int(data.get("confidence", 0) or 0),
        )
    raise ValueError("model output did not contain patch JSON")


def validate_patch_plan(plan: PatchPlan) -> None:
    if plan.confidence < MIN_CONFIDENCE:
        raise RuntimeError(f"patch confidence too low: {plan.confidence}")
    if not plan.edits:
        raise RuntimeError("patch plan has no edits")
    if not plan.commit_message.strip():
        raise RuntimeError("patch plan has no commit message")
    for edit in plan.edits:
        if not edit.path or not edit.search:
            raise RuntimeError("patch edit missing path or search text")


def apply_patch_plan(repo_path: Path, plan: PatchPlan) -> list[str]:
    touched: list[str] = []
    for edit in plan.edits:
        path = repo_path / edit.path
        if not path.is_file():
            raise RuntimeError(f"patch path not found: {edit.path}")
        old = path.read_text(encoding="utf-8", errors="replace")
        count = old.count(edit.search)
        if count != 1:
            raise RuntimeError(f"{edit.path}: search block matched {count} times")
        new = old.replace(edit.search, edit.replace, 1)
        deleted = max(0, len(old.splitlines()) - len(new.splitlines()))
        if deleted > 150:
            raise RuntimeError(f"{edit.path}: patch deletes too many lines ({deleted})")
        path.write_text(new, encoding="utf-8")
        touched.append(edit.path)
    return sorted(set(touched))


def default_test_commands(repo_path: Path) -> list[list[str]]:
    if (repo_path / "package.json").exists():
        return [["npm", "test"]]
    if (repo_path / "pyproject.toml").exists() or (repo_path / "pytest.ini").exists() or (repo_path / "setup.py").exists():
        return [["pytest", "-q"]]
    if (repo_path / "go.mod").exists():
        return [["go", "test", "./..."]]
    if (repo_path / "Cargo.toml").exists():
        return [["cargo", "test"]]
    return []


def run_verification(repo_path: Path, commands: list[list[str]]) -> tuple[bool, str]:
    commands = commands or default_test_commands(repo_path)
    if not commands:
        return True, "no local test command detected"
    messages: list[str] = []
    for cmd in commands[:3]:
        result = run_cmd(cmd, repo_path, timeout=600)
        label = " ".join(cmd)
        if result.returncode != 0:
            output = (result.stdout + result.stderr)[-2000:]
            return False, f"{label} failed: {output}"
        messages.append(f"{label} passed")
    return True, "; ".join(messages)


def generate_patch(
    repo: str,
    issue_number: int,
    issue_data: dict[str, Any],
    repo_path: Path,
) -> PatchPlan:
    repo_context = collect_repo_context(repo_path)
    error_context = ""
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        prompt = build_implementation_prompt(repo, issue_number, issue_data, repo_context)
        try:
            plan = parse_patch_plan(run_opencode(prompt))
            validate_patch_plan(plan)
            return plan
        except Exception as exc:
            error_context = str(exc)
            print(f"  patch attempt {attempt}/{MAX_MODEL_ATTEMPTS} failed: {exc}")
    raise RuntimeError(f"patch generation failed: {error_context}")


def build_pr_body_prompt(repo: str, issue_number: int, issue_data: dict[str, Any], plan: PatchPlan) -> str:
    return f"""Write a PR body for this contribution.

Rules:
- Professional and specific.
- Human developer tone, not chatbot tone.
- Reference the issue: #{issue_number}
- Explain what changed and why.
- Mention that tests were run if applicable.
- No em dash or en dash.
- No bullet lists.
- Avoid: furthermore, moreover, in conclusion, it's worth noting.
- Keep under 800 characters.

Repository: {repo}
Issue: {issue_data.get('title', '')}

Summary: {plan.summary}

Return only JSON:
{{
  "body": "PR body text"
}}
"""


def draft_pr_body(repo: str, issue_number: int, issue_data: dict[str, Any], plan: PatchPlan) -> str:
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            output = run_opencode(build_pr_body_prompt(repo, issue_number, issue_data, plan))
            matches = re.findall(r"\{.*\}", output, re.DOTALL)
            for block in reversed(matches):
                try:
                    data = json.loads(block)
                    body = str(data.get("body", "") or "")
                    ok, msg = lint_comment(body)
                    if ok:
                        return body
                    print(f"  PR body lint failed: {msg}")
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            print(f"  PR body draft attempt {attempt} failed: {exc}")
    raise RuntimeError("failed to draft PR body")


def build_issue_comment_prompt(repo: str, issue_number: int, issue_data: dict[str, Any]) -> str:
    return f"""Write a comment to post on this issue before opening a PR.

Rules:
- Professional and specific.
- Mention that you are working on it.
- Human developer tone, not chatbot tone.
- No em dash or en dash.
- No bullet lists.
- Avoid: furthermore, moreover, in conclusion, it's worth noting.
- Keep under 400 characters.

Issue: {issue_data.get('title', '')}

Return only JSON:
{{
  "body": "comment text"
}}
"""


def draft_issue_comment(repo: str, issue_number: int, issue_data: dict[str, Any]) -> str:
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            output = run_opencode(build_issue_comment_prompt(repo, issue_number, issue_data))
            matches = re.findall(r"\{.*\}", output, re.DOTALL)
            for block in reversed(matches):
                try:
                    data = json.loads(block)
                    body = str(data.get("body", "") or "")
                    ok, msg = lint_comment(body)
                    if ok:
                        return body
                    print(f"  issue comment lint failed: {msg}")
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            print(f"  issue comment draft attempt {attempt} failed: {exc}")
    raise RuntimeError("failed to draft issue comment")


def push_branch(repo_path: Path, branch: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would push branch {branch}")
        return
    result = run_cmd(["git", "push", "origin", branch], repo_path, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git push failed")


def open_pr(repo: str, branch: str, base: str, title: str, body: str, dry_run: bool) -> str:
    if dry_run:
        print(f"[dry-run] would open PR: {title}")
        return "https://github.com/dry-run/pr"
    hari_login = load_json(CONFIG_FILE, {}).get("user", {}).get("login", "Mr-Neutr0n")
    result = gh_text([
        "pr", "create",
        "-R", repo,
        "-t", title,
        "-b", body,
        "-H", f"{hari_login}:{branch}",
        "-B", base,
    ])
    # Extract URL from output
    for line in result.splitlines():
        if line.startswith("https://"):
            return line.strip()
    return result.strip()


def post_issue_comment(repo: str, issue_number: int, body: str, dry_run: bool) -> str | None:
    if dry_run:
        print(f"[dry-run] would comment on issue {repo}#{issue_number}")
        return None
    try:
        return gh_text(["issue", "comment", str(issue_number), "-R", repo, "--body", body]).strip()
    except Exception as exc:
        print(f"  failed to post issue comment: {exc}")
        return None


def add_to_queue(repo: str, pr_number: int) -> None:
    """Add newly opened PR to backlog queue."""
    queue_data = load_json(QUEUE_FILE, {"queue": []})
    if isinstance(queue_data, list):
        queue_data = {"queue": queue_data}
    queue_data.setdefault("queue", [])
    key = f"{repo}#{pr_number}"
    # Check if already in queue
    for item in queue_data["queue"]:
        if isinstance(item, str) and item == key:
            return
        if isinstance(item, dict) and item.get("key") == key:
            return
    queue_data["queue"].insert(0, key)
    write_json(QUEUE_FILE, queue_data)


def update_candidate_status(data: dict[str, Any], dedupe_key: str, status: str) -> None:
    for c in data.get("candidates", []):
        if c.get("dedupe_key") == dedupe_key:
            c["status"] = status
            c["selected_at"] = iso_now()
            break


def process_candidate(
    candidate: dict[str, Any],
    config: dict[str, Any],
    candidates_data: dict[str, Any],
    dry_run: bool,
) -> ActionRecord | None:
    """Process one candidate: implement, verify, push, open PR."""
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    hari_login = config.get("user", {}).get("login", "Mr-Neutr0n")
    repo = candidate["repo"]
    issue_number = candidate["issue_number"]
    dedupe_key = candidate["dedupe_key"]
    
    print(f"Processing {dedupe_key}")
    
    # Preflight
    ok, msg = run_preflight(repo)
    if not ok:
        print(f"  preflight blocked: {msg}")
        update_candidate_status(candidates_data, dedupe_key, "skipped")
        return ActionRecord("skip", dedupe_key, f"preflight blocked: {msg}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
    
    # Check for duplicate PRs
    if check_existing_prs(repo, issue_number, hari_login):
        print(f"  already has PR for this issue")
        update_candidate_status(candidates_data, dedupe_key, "skipped")
        return ActionRecord("skip", dedupe_key, "duplicate PR exists", 0, iso_now(), workflow_run_id, dry_run=dry_run)
    
    # Fetch issue details
    try:
        issue_data = fetch_issue(repo, issue_number)
    except Exception as exc:
        print(f"  failed to fetch issue: {exc}")
        update_candidate_status(candidates_data, dedupe_key, "failed")
        return ActionRecord("failed", dedupe_key, f"fetch issue failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
    
    # Fork repo
    try:
        fork_repo(repo)
    except Exception as exc:
        print(f"  fork failed: {exc}")
        update_candidate_status(candidates_data, dedupe_key, "failed")
        return ActionRecord("failed", dedupe_key, f"fork failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
    
    # Clone and create branch
    with tempfile.TemporaryDirectory() as tmp:
        try:
            repo_path = clone_repo(repo, Path(tmp), hari_login)
            base_branch = candidate.get("default_branch", "main")
            branch = create_branch(repo_path, base_branch, issue_number)
        except Exception as exc:
            print(f"  clone/branch failed: {exc}")
            update_candidate_status(candidates_data, dedupe_key, "failed")
            return ActionRecord("failed", dedupe_key, f"clone/branch failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        
        # Generate patch
        try:
            plan = generate_patch(repo, issue_number, issue_data, repo_path)
        except Exception as exc:
            print(f"  patch generation failed: {exc}")
            update_candidate_status(candidates_data, dedupe_key, "failed")
            return ActionRecord("failed", dedupe_key, f"patch generation failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        
        # Apply patch
        try:
            touched = apply_patch_plan(repo_path, plan)
        except Exception as exc:
            print(f"  patch application failed: {exc}")
            update_candidate_status(candidates_data, dedupe_key, "failed")
            return ActionRecord("failed", dedupe_key, f"patch application failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        
        # Verify
        try:
            ok, test_msg = run_verification(repo_path, plan.test_commands)
            if not ok:
                print(f"  verification failed: {test_msg}")
                update_candidate_status(candidates_data, dedupe_key, "failed")
                return ActionRecord("failed", dedupe_key, f"verification failed: {test_msg}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        except Exception as exc:
            print(f"  verification error: {exc}")
            update_candidate_status(candidates_data, dedupe_key, "failed")
            return ActionRecord("failed", dedupe_key, f"verification error: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        
        # Commit
        try:
            for rel in touched:
                result = run_cmd(["git", "add", rel], repo_path)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip())
            status = run_cmd(["git", "status", "--porcelain"], repo_path)
            if not status.stdout.strip():
                raise RuntimeError("patch produced no changes")
            commit = run_cmd(["git", "commit", "-m", plan.commit_message], repo_path)
            if commit.returncode != 0:
                raise RuntimeError(commit.stderr.strip() or "git commit failed")
            sha_result = run_cmd(["git", "rev-parse", "HEAD"], repo_path)
            commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None
        except Exception as exc:
            print(f"  commit failed: {exc}")
            update_candidate_status(candidates_data, dedupe_key, "failed")
            return ActionRecord("failed", dedupe_key, f"commit failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        
        # Push
        try:
            push_branch(repo_path, branch, dry_run)
        except Exception as exc:
            print(f"  push failed: {exc}")
            update_candidate_status(candidates_data, dedupe_key, "failed")
            return ActionRecord("failed", dedupe_key, f"push failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        
        # Draft PR body
        try:
            pr_body = draft_pr_body(repo, issue_number, issue_data, plan)
        except Exception as exc:
            print(f"  PR body draft failed: {exc}")
            pr_body = f"Fixes #{issue_number}"
        
        # Open PR
        pr_title = f"Fix: {issue_data.get('title', f'issue #{issue_number}')}"
        try:
            pr_url = open_pr(repo, branch, base_branch, pr_title, pr_body, dry_run)
        except Exception as exc:
            print(f"  open PR failed: {exc}")
            update_candidate_status(candidates_data, dedupe_key, "failed")
            return ActionRecord("failed", dedupe_key, f"open PR failed: {exc}", 0, iso_now(), workflow_run_id, dry_run=dry_run)
        
        # Extract PR number from URL
        pr_number = 0
        if pr_url:
            match = re.search(r"/(\d+)$", pr_url)
            if match:
                pr_number = int(match.group(1))
        
        # Comment on issue if appropriate
        if candidate.get("issue_comment_policy") == "comment_or_claim_then_open_pr":
            try:
                comment_body = draft_issue_comment(repo, issue_number, issue_data)
                post_issue_comment(repo, issue_number, comment_body, dry_run)
            except Exception as exc:
                print(f"  issue comment failed: {exc}")
        
        # Add to queue
        if pr_number and not dry_run:
            add_to_queue(repo, pr_number)
        
        update_candidate_status(candidates_data, dedupe_key, "contributed")
        
        return ActionRecord(
            "open_pr",
            dedupe_key,
            plan.summary,
            plan.confidence,
            iso_now(),
            workflow_run_id,
            pr_url=pr_url,
            commit_sha=commit_sha,
            state="contributed",
            dry_run=dry_run,
        )


def run_new_contributor(max_prs: int, dry_run: bool) -> int:
    """Run the new contributor workflow."""
    config = load_json(CONFIG_FILE, {})
    hari_login = config.get("user", {}).get("login", "Mr-Neutr0n")
    
    candidates_data = load_candidates()
    selected = select_candidates(candidates_data, max_prs)
    
    if not selected:
        print("No new candidates available")
        return 0
    
    records: list[ActionRecord] = []
    success_count = 0
    
    for candidate in selected:
        if success_count >= max_prs:
            break
        
        try:
            record = process_candidate(candidate, config, candidates_data, dry_run)
            if record:
                records.append(record)
                if not dry_run:
                    append_action(record)
                if record.type == "open_pr":
                    success_count += 1
        except Exception as exc:
            print(f"  unexpected error: {exc}")
            record = ActionRecord(
                "failed",
                candidate.get("dedupe_key", "unknown"),
                str(exc),
                0,
                iso_now(),
                os.environ.get("GITHUB_RUN_ID"),
                dry_run=dry_run,
            )
            records.append(record)
            if not dry_run:
                append_action(record)
        
        time.sleep(5)
    
    if not dry_run:
        candidates_data["_updated"] = iso_now()
        write_json(CANDIDATES_FILE, candidates_data)
    
    # Progress summary
    summary_lines = [
        "",
        f"## {utcnow().date().isoformat()} - New Contributor Batch",
        "",
        f"- Target: {max_prs}",
        f"- Successful: {success_count}",
        f"- Attempted: {len(records)}",
        f"- Dry run: {dry_run}",
        "",
        "Results:",
    ]
    for record in records:
        summary_lines.append(f"  - {record.target}: {record.type} - {record.reason}")
        if record.pr_url:
            summary_lines.append(f"    PR: {record.pr_url}")
    
    with PROGRESS_FILE.open("a") as f:
        f.write("\n".join(summary_lines) + "\n")
    
    print(f"New contributor batch complete: {success_count}/{max_prs} PRs opened")
    return 0 if success_count >= MIN_DAILY_PRS else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="New Contributor")
    parser.add_argument("--max-prs", type=int, default=MAX_DAILY_PRS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    return run_new_contributor(args.max_prs, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
