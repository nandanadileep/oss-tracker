#!/usr/bin/env python3
"""Backlog Steward automation.

Owns the existing PR queue: deterministic gates first, model second, state updates
last. This is intentionally conservative about writes: comments are allowed, close
is blocked in code, and fix actions are logged for the next phase.

Queue mechanics:
- queue is a FIFO list with a cursor index (not a rotating list)
- items are removed from queue when they go to cooldown or needs_attention
- expired cooldown items are promoted back to queue at the start of each run
- the cursor advances as items are processed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".oss-harness" / "state"
CONFIG_FILE = ROOT / ".oss-harness" / "config.json"
QUEUE_FILE = STATE_DIR / "queue.json"
PROGRESS_FILE = STATE_DIR / "progress.md"
ACTIONS_FILE = STATE_DIR / "actions.jsonl"
PREFLIGHT = ROOT / ".oss-harness" / "scanners" / "repo-preflight.py"

OPENCODE_MODEL = "zen/big-pickle"
MAX_MODEL_ATTEMPTS = 3
NUDGE_AFTER_DAYS = 7
DEFAULT_TRACKER_REPO = "Mr-Neutr0n/oss-tracker"
HEALTH_CHECK_WINDOW = 3  # number of consecutive runs to check for stuck queue

MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
BOT_LOGINS = {"github-actions[bot]", "dependabot[bot]", "codecov[bot]"}
CLA_TOKENS = ("cla", "dco", "sign-off", "signoff", "license agreement")
BANNED_COMMENT_PHRASES = (
    "furthermore",
    "moreover",
    "in conclusion",
    "it's worth noting",
)


@dataclass
class Activity:
    latest_author: str | None
    latest_author_association: str | None
    latest_body: str
    latest_created_at: str | None
    last_hari_comment_at: str | None
    last_maintainer_comment_at: str | None
    should_nudge: bool
    should_reply: bool
    next_review_at: str | None
    reason: str


@dataclass
class Decision:
    action: str
    reason: str
    confidence: int
    comment_body: str = ""
    requires_human: bool = False


@dataclass
class MessageDraft:
    action: str
    body: str
    reason: str
    confidence: int
    generated_by: str
    attempt: int


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
    local_issue_url: str | None = None
    commit_sha: str | None = None
    state: str | None = None
    dry_run: bool = False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat().replace("+00:00", "Z")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def gh_json(args: list[str], *, timeout: int = 60) -> Any:
    result = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh command failed")
    return json.loads(result.stdout or "null")


def gh_text(args: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh command failed")
    return result.stdout


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def load_config() -> dict[str, Any]:
    return load_json(CONFIG_FILE, {})


def user_login(config: dict[str, Any]) -> str:
    return config.get("user", {}).get("login", "Mr-Neutr0n")


def item_key(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("key") or item.get("pr")
    return None


def parse_pr_key(key: str) -> tuple[str, int]:
    match = re.match(r"^([^/]+/[^#]+)#(\d+)$", key)
    if not match:
        raise ValueError(f"invalid PR key: {key}")
    return match.group(1), int(match.group(2))


def normalize_queue_state(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        return {"_version": "0.4.0", "queue": raw, "cooldown": [], "needs_attention": [], "done": [], "_cursor": 0}
    if not isinstance(raw, dict):
        return {"_version": "0.4.0", "queue": [], "cooldown": [], "needs_attention": [], "done": [], "_cursor": 0}
    raw.setdefault("queue", [])
    raw.setdefault("cooldown", [])
    raw.setdefault("needs_attention", [])
    raw.setdefault("done", [])
    raw.setdefault("_cursor", 0)
    return raw


def remove_from_queue(state: dict[str, Any], key: str) -> None:
    state["queue"] = [item for item in state.get("queue", []) if item_key(item) != key]


def append_unique_list(state: dict[str, Any], list_name: str, entry: dict[str, Any]) -> None:
    entries = state.setdefault(list_name, [])
    key = entry.get("key")
    if key:
        entries[:] = [existing for existing in entries if item_key(existing) != key]
    entries.append(entry)


def set_cooldown(state: dict[str, Any], key: str, reason: str, next_review_at: str) -> None:
    remove_from_queue(state, key)
    append_unique_list(
        state,
        "cooldown",
        {
            "key": key,
            "reason": reason,
            "last_action_at": iso_now(),
            "next_review_at": next_review_at,
        },
    )


def set_needs_attention(state: dict[str, Any], key: str, reason: str, kind: str) -> None:
    remove_from_queue(state, key)
    append_unique_list(
        state,
        "needs_attention",
        {"key": key, "kind": kind, "reason": reason, "created_at": iso_now()},
    )


def promote_expired_cooldown(state: dict[str, Any], now: datetime) -> int:
    """Move expired cooldown items back to queue. Returns count promoted."""
    promoted = 0
    remaining_cooldown = []
    for item in state.get("cooldown", []):
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        next_review = parse_dt(item.get("next_review_at"))
        if key and next_review and next_review <= now:
            state["queue"].append(key)
            promoted += 1
            print(f"  promoted from cooldown: {key}")
        else:
            remaining_cooldown.append(item)
    state["cooldown"] = remaining_cooldown
    return promoted


def select_batch(state: dict[str, Any], batch_size: int, now: datetime) -> list[str]:
    """Select next batch starting from cursor, advancing cursor as we go."""
    selected: list[str] = []
    blocked = {item_key(item) for item in state.get("needs_attention", [])}
    queue = state.get("queue", [])
    base_cursor = state.get("_cursor", 0)
    total = len(queue)
    if total == 0:
        return selected
    
    # Ensure cursor is valid
    base_cursor = base_cursor % total
    
    # Wrap around at most once
    checked = 0
    next_cursor = base_cursor
    while checked < total and len(selected) < batch_size:
        idx = (base_cursor + checked) % total
        item = queue[idx]
        key = item_key(item)
        checked += 1
        if not key or key in blocked:
            continue
        selected.append(key)
        next_cursor = (idx + 1) % total
    
    state["_cursor"] = next_cursor
    return selected


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


def fetch_pr(repo: str, number: int) -> dict[str, Any]:
    return gh_json(
        [
            "pr",
            "view",
            str(number),
            "-R",
            repo,
            "--json",
            "url,title,author,state,mergeable,headRefName,headRepositoryOwner,baseRefName,updatedAt,createdAt,comments,reviews,files,additions,deletions,labels,statusCheckRollup",
        ]
    )


def fetch_issue_comments(repo: str, number: int) -> list[dict[str, Any]]:
    try:
        return gh_json(["api", f"repos/{repo}/issues/{number}/comments", "--paginate"])
    except Exception:
        return []


def is_bot(login: str | None) -> bool:
    if not login:
        return False
    return login.endswith("[bot]") or login in BOT_LOGINS


def is_maintainer_comment(comment: dict[str, Any], hari_login: str) -> bool:
    user = comment.get("user") or comment.get("author") or {}
    login = user.get("login") if isinstance(user, dict) else None
    association = comment.get("author_association") or comment.get("authorAssociation")
    return bool(login and login != hari_login and association in MAINTAINER_ASSOCIATIONS and not is_bot(login))


def comment_login(comment: dict[str, Any]) -> str | None:
    user = comment.get("user") or comment.get("author") or {}
    return user.get("login") if isinstance(user, dict) else None


def classify_activity(
    pr_data: dict[str, Any], comments: list[dict[str, Any]], hari_login: str, now: datetime
) -> Activity:
    sorted_comments = sorted(
        comments,
        key=lambda c: parse_dt(c.get("created_at") or c.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    latest = sorted_comments[-1] if sorted_comments else None
    latest_author = comment_login(latest) if latest else None
    latest_at = (latest or {}).get("created_at") or (latest or {}).get("createdAt")
    latest_body = (latest or {}).get("body", "") if latest else ""
    latest_assoc = (latest or {}).get("author_association") or (latest or {}).get("authorAssociation")

    hari_times = [
        parse_dt(c.get("created_at") or c.get("createdAt"))
        for c in sorted_comments
        if comment_login(c) == hari_login
    ]
    maintainer_times = [
        parse_dt(c.get("created_at") or c.get("createdAt"))
        for c in sorted_comments
        if is_maintainer_comment(c, hari_login)
    ]
    hari_times = [dt for dt in hari_times if dt]
    maintainer_times = [dt for dt in maintainer_times if dt]
    last_hari = max(hari_times) if hari_times else None
    last_maintainer = max(maintainer_times) if maintainer_times else None

    updated_at = parse_dt(pr_data.get("updatedAt"))
    age_basis = parse_dt(latest_at) or updated_at or now
    next_review = age_basis + timedelta(days=NUDGE_AFTER_DAYS)

    if latest and latest_author == hari_login:
        should_nudge = next_review <= now
        reason = "latest comment is Hari's and threshold passed" if should_nudge else "latest comment is Hari's but still in cooldown"
    elif not latest:
        should_nudge = next_review <= now
        reason = "no comments and PR is past initial threshold" if should_nudge else "no comments but PR is still in initial cooldown"
    else:
        should_nudge = False
        reason = "latest comment is not Hari's"

    should_reply = bool(last_maintainer and (not last_hari or last_maintainer > last_hari))
    if should_reply:
        reason = "maintainer or reviewer replied after Hari"

    return Activity(
        latest_author=latest_author,
        latest_author_association=latest_assoc,
        latest_body=latest_body,
        latest_created_at=latest_at,
        last_hari_comment_at=last_hari.isoformat().replace("+00:00", "Z") if last_hari else None,
        last_maintainer_comment_at=last_maintainer.isoformat().replace("+00:00", "Z") if last_maintainer else None,
        should_nudge=should_nudge,
        should_reply=should_reply,
        next_review_at=next_review.isoformat().replace("+00:00", "Z"),
        reason=reason,
    )


def check_summary(pr_data: dict[str, Any]) -> tuple[str, bool, list[str]]:
    checks = pr_data.get("statusCheckRollup") or []
    failed_names: list[str] = []
    pending = 0
    for check in checks:
        conclusion = check.get("conclusion")
        status = check.get("status")
        name = check.get("name") or check.get("context") or check.get("workflowName") or "unknown"
        if conclusion in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
            failed_names.append(name)
        elif status in {"IN_PROGRESS", "QUEUED", "PENDING"}:
            pending += 1
    total = len(checks)
    if failed_names:
        return f"{len(failed_names)}/{total} failing: {', '.join(failed_names[:5])}", False, failed_names
    if pending:
        return f"{pending}/{total} pending", False, failed_names
    if total:
        return f"{total}/{total} passing", True, failed_names
    return "no checks", True, failed_names


def has_cla_blocker(pr_data: dict[str, Any]) -> bool:
    text_parts: list[str] = []
    for check in pr_data.get("statusCheckRollup") or []:
        if check.get("conclusion") in {"FAILURE", "ERROR"}:
            text_parts.append(str(check.get("name") or check.get("context") or ""))
    for label in pr_data.get("labels") or []:
        text_parts.append(str(label.get("name", "")))
    text = " ".join(text_parts).lower()
    return any(token in text for token in CLA_TOKENS)


def build_model_prompt(key: str, pr_data: dict[str, Any], activity: Activity, ci_status: str) -> str:
    files = pr_data.get("files") or []
    file_names = ", ".join(f.get("path", "unknown") for f in files[:12])
    mode = "reply" if activity.should_reply else "nudge"
    latest = activity.latest_body[:1200] if activity.latest_body else "(none)"
    return f"""You are Hari's OSS backlog steward. Decide the next safe action for this PR.

Hard policy:
- Closing PRs is disabled. Never return close.
- If the maintainer replied, write a specific direct reply, not a generic bump.
- If nudging, mention concrete PR facts. Do not claim CI is green unless CI status says passing or no checks.
- Keep comments under 600 characters.
- No em dash or en dash. Use commas or hyphens.
- No bullet lists in comments.
- Avoid AI-sounding phrases like furthermore, moreover, in conclusion, it's worth noting.

Preferred action for this PR: {mode}

PR:
- Key: {key}
- Title: {pr_data.get('title')}
- Author: {(pr_data.get('author') or {}).get('login')}
- State: {pr_data.get('state')}
- Mergeable: {pr_data.get('mergeable')}
- CI: {ci_status}
- Files: {file_names or '(none)'}
- Diff size: +{pr_data.get('additions', 0)}/-{pr_data.get('deletions', 0)}

Activity:
- Latest author: {activity.latest_author}
- Latest association: {activity.latest_author_association}
- Latest comment at: {activity.latest_created_at}
- Last Hari comment: {activity.last_hari_comment_at}
- Last maintainer comment: {activity.last_maintainer_comment_at}
- Activity reason: {activity.reason}

Latest comment:
{latest}

Return only JSON with this schema:
{{
  "action": "nudge|reply|fix|skip|defer",
  "reason": "short reason",
  "confidence": 1-10,
  "comment_body": "comment for nudge/reply, empty otherwise",
  "requires_human": false
}}
"""


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


def parse_decision(output: str) -> Decision:
    matches = re.findall(r"\{.*\}", output, re.DOTALL)
    for block in reversed(matches):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        action = str(data.get("action", "skip")).lower()
        if action not in {"nudge", "reply", "fix", "skip", "defer", "close"}:
            action = "skip"
        return Decision(
            action=action,
            reason=str(data.get("reason", "")),
            confidence=int(data.get("confidence", 0) or 0),
            comment_body=str(data.get("comment_body", "") or ""),
            requires_human=bool(data.get("requires_human", False)),
        )
    raise ValueError("model output did not contain decision JSON")


def model_decision(key: str, pr_data: dict[str, Any], activity: Activity, ci_status: str) -> Decision:
    prompt = build_model_prompt(key, pr_data, activity, ci_status)
    last_error: Exception | None = None
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            return parse_decision(run_opencode(prompt))
        except Exception as exc:
            last_error = exc
            print(f"  model attempt {attempt}/{MAX_MODEL_ATTEMPTS} failed: {exc}")
            time.sleep(2)
    return Decision("skip", f"model failed: {last_error}", 0, requires_human=True)


def build_message_prompt(
    required_action: str,
    key: str,
    pr_data: dict[str, Any],
    activity: Activity,
    ci_status: str,
    lint_error: str = "",
    extra_context: str = "",
) -> str:
    files = pr_data.get("files") or []
    file_names = ", ".join(f.get("path", "unknown") for f in files[:12])
    latest = activity.latest_body[:1200] if activity.latest_body else "(none)"
    retry = f"\nPrevious draft failed lint: {lint_error}\nRewrite it so it passes.\n" if lint_error else ""
    return f"""Write the public-facing text for Hari's OSS automation.

You only write text. Do not change the action.

Required action: {required_action}
{retry}
Style rules:
- Professional and specific.
- Human developer tone, not chatbot tone.
- Under 600 characters for PR comments.
- No em dash or en dash.
- No bullet list.
- Avoid: furthermore, moreover, in conclusion, it's worth noting.
- For upstream PR comments, do not offer to close the PR.
- Mention at least one concrete fact from the PR.

PR:
- Key: {key}
- Title: {pr_data.get('title')}
- CI: {ci_status}
- Files: {file_names or '(none)'}
- Diff size: +{pr_data.get('additions', 0)}/-{pr_data.get('deletions', 0)}
- Latest author: {activity.latest_author}
- Latest association: {activity.latest_author_association}
- Activity reason: {activity.reason}

Latest comment:
{latest}

Extra context:
{extra_context or '(none)'}

Return only JSON:
{{
  "action": "{required_action}",
  "body": "text to post",
  "reason": "why this text fits",
  "confidence": 1-10
}}
"""


def parse_message_draft(output: str, required_action: str, attempt: int) -> MessageDraft:
    matches = re.findall(r"\{.*\}", output, re.DOTALL)
    for block in reversed(matches):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        return MessageDraft(
            action=required_action,
            body=str(data.get("body", "") or ""),
            reason=str(data.get("reason", "") or ""),
            confidence=int(data.get("confidence", 0) or 0),
            generated_by=OPENCODE_MODEL,
            attempt=attempt,
        )
    raise ValueError("model output did not contain message JSON")


def draft_message(
    required_action: str,
    key: str,
    pr_data: dict[str, Any],
    activity: Activity,
    ci_status: str,
    extra_context: str = "",
    max_attempts: int = 2,
) -> MessageDraft:
    lint_error = ""
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        prompt = build_message_prompt(required_action, key, pr_data, activity, ci_status, lint_error, extra_context)
        try:
            draft = parse_message_draft(run_opencode(prompt), required_action, attempt)
        except Exception as exc:
            last_error = str(exc)
            lint_error = last_error
            continue
        ok, lint_error = lint_comment(draft.body)
        if ok and draft.confidence >= 5:
            return draft
        if not ok:
            last_error = lint_error
        else:
            last_error = f"low draft confidence: {draft.confidence}"
            lint_error = last_error
    raise RuntimeError(f"message drafting failed: {last_error}")


def lint_comment(body: str, *, allow_close_word: bool = False) -> tuple[bool, str]:
    stripped = body.strip()
    if not stripped:
        return False, "empty comment"
    if len(stripped) > 600:
        return False, "comment exceeds 600 chars"
    if "—" in stripped or "–" in stripped:
        return False, "comment contains em/en dash"
    lowered = stripped.lower()
    for phrase in BANNED_COMMENT_PHRASES:
        if phrase in lowered:
            return False, f"comment contains banned phrase: {phrase}"
    if not allow_close_word and re.search(r"\bclose\b|\bclosing\b", lowered):
        return False, "comment mentions closing, which is disabled by policy"
    for line in stripped.splitlines():
        if re.match(r"^\s*([-*]|\d+[.)])\s+", line):
            return False, "comment contains bullet/list formatting"
    return True, "ok"


def post_comment(repo: str, number: int, body: str, dry_run: bool) -> str | None:
    if dry_run:
        print(f"[dry-run] would comment on {repo}#{number}: {body[:120]}")
        return None
    gh_text(["pr", "comment", str(number), "-R", repo, "--body", body])
    return None


def run_cmd(cmd: list[str], cwd: Path, *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def head_owner_login(pr_data: dict[str, Any]) -> str | None:
    owner = pr_data.get("headRepositoryOwner") or {}
    return owner.get("login") if isinstance(owner, dict) else None


def clone_fork_for_pr(repo: str, pr_data: dict[str, Any], workdir: Path, hari_login: str) -> Path:
    upstream_owner, upstream_repo = repo.split("/", 1)
    head_owner = head_owner_login(pr_data)
    if head_owner != hari_login:
        raise RuntimeError(f"refusing fix: PR head owner is {head_owner}, not {hari_login}")
    clone_repo = f"{hari_login}/{upstream_repo}"
    clone_url = f"https://x-access-token:{os.environ.get('GITHUB_TOKEN', '')}@github.com/{clone_repo}.git"
    repo_path = workdir / upstream_repo
    result = subprocess.run(["git", "clone", clone_url, str(repo_path)], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git clone failed")
    head_branch = pr_data.get("headRefName")
    if not head_branch:
        raise RuntimeError("PR has no headRefName")
    upstream_url = f"https://github.com/{upstream_owner}/{upstream_repo}.git"
    run_cmd(["git", "remote", "add", "upstream", upstream_url], repo_path)
    result = run_cmd(["git", "checkout", head_branch], repo_path)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git checkout failed")
    return repo_path


def collect_patch_context(repo_path: Path, pr_data: dict[str, Any], max_chars: int = 50000) -> str:
    sections: list[str] = []
    used = 0
    for file_info in pr_data.get("files") or []:
        rel = file_info.get("path")
        if not rel:
            continue
        path = repo_path / rel
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        block = f"--- {rel} ---\n{content[:12000]}"
        if used + len(block) > max_chars:
            break
        sections.append(block)
        used += len(block)
    return "\n\n".join(sections) if sections else "(no changed files available)"


def build_patch_prompt(
    key: str,
    pr_data: dict[str, Any],
    failed_checks: list[str],
    activity: Activity,
    file_context: str,
    error_context: str = "",
) -> str:
    retry = f"\nPrevious attempt failed:\n{error_context}\n" if error_context else ""
    latest = activity.latest_body[:1200] if activity.latest_body else "(none)"
    return f"""Generate a minimal code fix for Hari's existing PR branch.

Rules:
- Return JSON only.
- Make the smallest targeted change.
- Only edit files shown in repository context.
- Use exact search/replace edits. The search text must appear exactly once.
- Do not remove unrelated code.
- Commit message must be professional and specific.
- Test commands should be commands worth running locally, as arrays of strings.
{retry}
PR:
- Key: {key}
- Title: {pr_data.get('title')}
- Failing checks: {', '.join(failed_checks) or '(none)'}

Latest maintainer/reviewer comment:
{latest}

Repository context:
{file_context}

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
    if plan.confidence < 5:
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


def generate_patch_plan(
    key: str,
    pr_data: dict[str, Any],
    failed_checks: list[str],
    activity: Activity,
    repo_path: Path,
) -> PatchPlan:
    file_context = collect_patch_context(repo_path, pr_data)
    error_context = ""
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        prompt = build_patch_prompt(key, pr_data, failed_checks, activity, file_context, error_context)
        try:
            plan = parse_patch_plan(run_opencode(prompt))
            validate_patch_plan(plan)
            return plan
        except Exception as exc:
            error_context = str(exc)
            print(f"  patch attempt {attempt}/{MAX_MODEL_ATTEMPTS} failed: {exc}")
    raise RuntimeError(f"patch generation failed: {error_context}")


def run_fix_loop(
    key: str,
    repo: str,
    number: int,
    pr_data: dict[str, Any],
    activity: Activity,
    ci_status: str,
    failed_checks: list[str],
    hari_login: str,
    dry_run: bool,
) -> tuple[ActionRecord, MessageDraft | None]:
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    if dry_run:
        return (
            ActionRecord("fix", key, "dry-run would attempt fork-only fix loop", 7, iso_now(), workflow_run_id, state="dry_run", dry_run=True),
            None,
        )
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = clone_fork_for_pr(repo, pr_data, Path(tmp), hari_login)
        plan = generate_patch_plan(key, pr_data, failed_checks, activity, repo_path)
        touched = apply_patch_plan(repo_path, plan)
        ok, test_msg = run_verification(repo_path, plan.test_commands)
        if not ok:
            raise RuntimeError(test_msg)
        for rel in touched:
            result = run_cmd(["git", "add", rel], repo_path)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f"git add failed for {rel}")
        status = run_cmd(["git", "status", "--porcelain"], repo_path)
        if not status.stdout.strip():
            raise RuntimeError("patch produced no changes")
        commit = run_cmd(["git", "commit", "-m", plan.commit_message], repo_path)
        if commit.returncode != 0:
            raise RuntimeError(commit.stderr.strip() or "git commit failed")
        push = run_cmd(["git", "push", "origin", str(pr_data.get("headRefName"))], repo_path, timeout=600)
        if push.returncode != 0:
            raise RuntimeError(push.stderr.strip() or "git push failed")
        sha = run_cmd(["git", "rev-parse", "HEAD"], repo_path)
        commit_sha = sha.stdout.strip() if sha.returncode == 0 else None
        draft = draft_message(
            "reply",
            key,
            pr_data,
            activity,
            ci_status,
            extra_context=f"Pushed commit {commit_sha or '(unknown)'} with summary: {plan.summary}. Verification: {test_msg}",
        )
        post_comment(repo, number, draft.body, dry_run=False)
        return (
            ActionRecord("fix", key, plan.summary, plan.confidence, iso_now(), workflow_run_id, commit_sha=commit_sha, state="cooldown"),
            draft,
        )


def existing_issue_url(search: str, tracker_repo: str) -> str | None:
    try:
        issues = gh_json(
            ["issue", "list", "-R", tracker_repo, "--state", "open", "--search", search, "--json", "title,url", "--limit", "20"]
        )
    except Exception:
        return None
    for issue in issues or []:
        if search in (issue.get("title") or ""):
            return issue.get("url")
    return None


def build_local_issue_prompt(key: str, reason: str, pr_data: dict[str, Any]) -> str:
    return f"""Write an internal oss-tracker issue for Hari.

The issue is not for upstream maintainers. It tells Hari that a human-only CLA/DCO/signature step blocks this PR.

Rules:
- Professional and concise.
- No em dash or en dash.
- No bullet lists.
- Title must include this exact PR key: {key}
- Body must explain the blocker and say Hari should close the local issue after resolving it.

PR:
- Key: {key}
- Title: {pr_data.get('title')}
- Reason: {reason}

Return only JSON:
{{
  "title": "issue title",
  "body": "issue body"
}}
"""


def draft_local_issue(key: str, reason: str, pr_data: dict[str, Any]) -> tuple[str, str]:
    output = run_opencode(build_local_issue_prompt(key, reason, pr_data))
    matches = re.findall(r"\{.*\}", output, re.DOTALL)
    for block in reversed(matches):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        title = str(data.get("title", "") or "")
        body = str(data.get("body", "") or "")
        if key not in title:
            raise RuntimeError("local issue title missing PR key")
        ok, lint_msg = lint_comment(body, allow_close_word=True)
        if not ok:
            raise RuntimeError(f"local issue body failed lint: {lint_msg}")
        return title, body
    raise RuntimeError("model output did not contain local issue JSON")


def create_local_issue(key: str, reason: str, pr_data: dict[str, Any], dry_run: bool) -> str | None:
    tracker_repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_TRACKER_REPO)
    existing = existing_issue_url(key, tracker_repo)
    if existing:
        return existing
    title, body = draft_local_issue(key, reason, pr_data)
    if dry_run:
        print(f"[dry-run] would create issue in {tracker_repo}: {title}")
        return None
    try:
        return gh_text(["issue", "create", "-R", tracker_repo, "-t", title, "-b", body]).strip()
    except Exception as exc:
        print(f"  failed to create local issue: {exc}")
        return None


def append_action(record: ActionRecord) -> None:
    ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ACTIONS_FILE.open("a") as f:
        f.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def progress_summary(records: list[ActionRecord], batch_size: int) -> None:
    lines = [
        "",
        f"## {utcnow().date().isoformat()} - Daily Automation Batch",
        "",
        f"- Batch size: {batch_size}",
        f"- Processed: {len(records)}",
    ]
    counts: dict[str, int] = {}
    for record in records:
        counts[record.type] = counts.get(record.type, 0) + 1
    for action, count in sorted(counts.items()):
        lines.append(f"- {action}: {count}")
    lines.append("")
    lines.append("PRs touched:")
    for record in records:
        lines.append(f"  - {record.target}: {record.type} - {record.reason}")
    with PROGRESS_FILE.open("a") as f:
        f.write("\n".join(lines) + "\n")


def deterministic_decision(
    key: str, pr_data: dict[str, Any], activity: Activity, ci_green: bool, failed_checks: list[str]
) -> Decision | None:
    if has_cla_blocker(pr_data):
        return Decision("defer", "CLA/DCO blocker detected", 10, requires_human=True)
    if pr_data.get("state") != "OPEN":
        return Decision("skip", f"PR state is {pr_data.get('state')}", 10)
    if activity.should_reply:
        return None
    if failed_checks:
        return Decision("fix", f"CI failing: {', '.join(failed_checks[:5])}", 7)
    if activity.should_nudge:
        return None
    return Decision("skip", activity.reason, 9)


def execute_decision(
    key: str,
    repo: str,
    number: int,
    decision: Decision,
    pr_data: dict[str, Any],
    activity: Activity,
    ci_status: str,
    failed_checks: list[str],
    config: dict[str, Any],
    state: dict[str, Any],
    dry_run: bool,
) -> ActionRecord:
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    if decision.action == "close":
        decision = Decision("skip", "close is disabled by policy", 10)

    if decision.confidence < 5 and decision.action not in {"skip", "defer"}:
        decision = Decision("defer", f"low confidence: {decision.reason}", decision.confidence)

    if decision.action in {"nudge", "reply"}:
        ok, preflight_msg = run_preflight(repo)
        if not ok:
            set_needs_attention(state, key, preflight_msg, "preflight_blocked")
            return ActionRecord("defer", key, "preflight blocked", decision.confidence, iso_now(), workflow_run_id, state="needs_attention", dry_run=dry_run)
        ok, lint_msg = lint_comment(decision.comment_body)
        if not ok:
            set_needs_attention(state, key, lint_msg, "comment_lint_failed")
            return ActionRecord("defer", key, lint_msg, decision.confidence, iso_now(), workflow_run_id, state="needs_attention", dry_run=dry_run)
        post_comment(repo, number, decision.comment_body, dry_run)
        next_review = (utcnow() + timedelta(days=NUDGE_AFTER_DAYS)).isoformat().replace("+00:00", "Z")
        set_cooldown(state, key, decision.action, next_review)
        return ActionRecord(decision.action, key, decision.reason, decision.confidence, iso_now(), workflow_run_id, state="cooldown", dry_run=dry_run)

    if decision.action == "fix":
        try:
            record, _draft = run_fix_loop(
                key,
                repo,
                number,
                pr_data,
                activity,
                ci_status,
                failed_checks,
                user_login(config),
                dry_run,
            )
            if record.state == "cooldown":
                next_review = (utcnow() + timedelta(days=NUDGE_AFTER_DAYS)).isoformat().replace("+00:00", "Z")
                set_cooldown(state, key, "fix", next_review)
            elif dry_run:
                remove_from_queue(state, key)
            return record
        except Exception as exc:
            set_needs_attention(state, key, str(exc), "fix_failed")
            return ActionRecord("defer", key, f"fix failed: {exc}", decision.confidence, iso_now(), workflow_run_id, state="needs_attention", dry_run=dry_run)

    if decision.action == "defer" and decision.requires_human and "cla" in decision.reason.lower():
        issue_url = create_local_issue(key, decision.reason, pr_data, dry_run)
        set_needs_attention(state, key, decision.reason, "cla_blocked")
        return ActionRecord("create_local_issue", key, decision.reason, decision.confidence, iso_now(), workflow_run_id, local_issue_url=issue_url, state="cla_blocked", dry_run=dry_run)

    if decision.action == "defer":
        set_needs_attention(state, key, decision.reason, "deferred")
        return ActionRecord("defer", key, decision.reason, decision.confidence, iso_now(), workflow_run_id, state="needs_attention", dry_run=dry_run)

    # For "skip" actions: if the reason is cooldown-related, put it in cooldown instead of leaving it in queue
    if "cooldown" in decision.reason.lower():
        next_review = (utcnow() + timedelta(days=NUDGE_AFTER_DAYS)).isoformat().replace("+00:00", "Z")
        set_cooldown(state, key, decision.reason, next_review)
        return ActionRecord("skip", key, decision.reason, decision.confidence, iso_now(), workflow_run_id, state="cooldown", dry_run=dry_run)
    
    # Remove from queue for other skip reasons (closed PR, etc.)
    remove_from_queue(state, key)
    return ActionRecord("skip", key, decision.reason, decision.confidence, iso_now(), workflow_run_id, state="skipped", dry_run=dry_run)


def process_one(key: str, state: dict[str, Any], config: dict[str, Any], dry_run: bool) -> ActionRecord:
    repo, number = parse_pr_key(key)
    print(f"Processing {key}")
    pr_data = fetch_pr(repo, number)
    comments = fetch_issue_comments(repo, number)
    activity = classify_activity(pr_data, comments, user_login(config), utcnow())
    ci_status, ci_green, failed_checks = check_summary(pr_data)
    print(f"  activity: {activity.reason}")
    print(f"  ci: {ci_status}")

    decision = deterministic_decision(key, pr_data, activity, ci_green, failed_checks)
    if decision is None:
        if activity.should_nudge and ci_green:
            draft = draft_message("nudge", key, pr_data, activity, ci_status)
            decision = Decision("nudge", draft.reason, draft.confidence, draft.body)
        elif activity.should_reply:
            draft = draft_message("reply", key, pr_data, activity, ci_status)
            decision = Decision("reply", draft.reason, draft.confidence, draft.body)
        else:
            decision = model_decision(key, pr_data, activity, ci_status)
    print(f"  decision: {decision.action} ({decision.confidence}/10) - {decision.reason}")
    return execute_decision(key, repo, number, decision, pr_data, activity, ci_status, failed_checks, config, state, dry_run)


def health_check(state: dict[str, Any], selected: list[str]) -> bool:
    """Check if the queue is stuck processing the same items repeatedly."""
    history = state.get("_last_selected", [])
    history.append(selected)
    history = history[-HEALTH_CHECK_WINDOW:]
    state["_last_selected"] = history
    
    if len(history) < HEALTH_CHECK_WINDOW:
        return True
    
    # Check if all recent runs selected the same items
    first = set(history[0])
    for batch in history[1:]:
        if set(batch) != first:
            return True
    
    print(f"  HEALTH CHECK FAILED: same items selected for {HEALTH_CHECK_WINDOW} consecutive runs")
    print(f"  Stuck items: {sorted(first)}")
    return False


def run(batch_size: int, dry_run: bool) -> int:
    config = load_config()
    state = normalize_queue_state(load_json(QUEUE_FILE, {}))
    now = utcnow()
    
    # Promote expired cooldown items back to queue
    promoted = promote_expired_cooldown(state, now)
    if promoted:
        print(f"Promoted {promoted} expired cooldown items back to queue")
    
    selected = select_batch(state, batch_size, now)
    if not selected:
        print("No actionable queue items found")
        return 0
    
    # Health check
    healthy = health_check(state, selected)
    if not healthy:
        print("Queue health check failed — items are stuck. Halting batch.")
        return 1

    records: list[ActionRecord] = []
    failures = 0
    for key in selected:
        try:
            record = process_one(key, state, config, dry_run)
            records.append(record)
            if not dry_run:
                append_action(record)
        except Exception as exc:
            failures += 1
            record = ActionRecord("failed", key, str(exc), 0, iso_now(), os.environ.get("GITHUB_RUN_ID"), dry_run=dry_run)
            records.append(record)
            if not dry_run:
                append_action(record)
            print(f"  failed: {exc}")
        if key != selected[-1]:
            time.sleep(5)

    if not dry_run:
        state["_updated"] = iso_now()
        write_json(QUEUE_FILE, state)
        progress_summary(records, batch_size)

    print("Batch summary")
    for record in records:
        print(f"  {record.target}: {record.type} - {record.reason}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backlog Steward")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return run(args.batch_size, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
