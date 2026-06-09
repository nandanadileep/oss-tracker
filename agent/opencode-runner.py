#!/usr/bin/env python3
"""
OSS Agent - Opencode Runner

Wraps the `opencode` CLI in headless mode to analyze PRs and execute decisions.
Runs on a self-hosted GitHub Actions runner where `opencode` is already configured.

Usage:
    python agent/opencode-runner.py <repo> <pr_number> [--dry-run]

Example:
    python agent/opencode-runner.py pytorch/vision 9384
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

# ── Configuration ───────────────────────────────────────────────────────────

MAX_ATTEMPTS = 3          # Max analysis attempts per PR
OPENCODE_PORT = 4096      # Port for opencode serve
OPENCODE_MODEL = "zen/big-pickle"  # Free tier model via OpenCode Zen
OPENCODE_ATTACH = f"http://localhost:{OPENCODE_PORT}"

# Hard-coded safety: repos we never write to directly
UPSTREAM_REPOS = set()  # Populated from preflight check

# ── Data Models ─────────────────────────────────────────────────────────────

@dataclass
class PRContext:
    repo: str
    number: int
    title: str
    author: str
    state: str
    mergeable: Optional[bool]
    ci_status: str
    comments: list
    reviews: list
    files_changed: int
    additions: int
    deletions: int
    stale_days: int
    tier: str
    last_updated: str

@dataclass
class Decision:
    action: str  # "nudge", "fix", "close", "skip", "defer"
    reason: str
    confidence: int  # 1-10
    comment_body: Optional[str] = None
    code_changes: Optional[list] = None
    requires_human: bool = False

# ── GitHub CLI Helpers ──────────────────────────────────────────────────────

def gh_api(args: list) -> dict:
    """Run `gh api` and return JSON."""
    cmd = ["gh", "api"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr}")
    return json.loads(result.stdout)

def gh_pr_view(repo: str, number: int) -> dict:
    """Fetch PR details via gh CLI."""
    cmd = [
        "gh", "pr", "view", str(number),
        "-R", repo,
        "--json", "url,title,author,state,mergeable,headRefName,baseRefName,updatedAt,createdAt,comments,reviews,files,additions,deletions,labels,statusCheckRollup"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr}")
    return json.loads(result.stdout)

def gh_comment(repo: str, number: int, body: str) -> None:
    """Post a comment on a PR."""
    cmd = [
        "gh", "pr", "comment", str(number),
        "-R", repo,
        "--body", body
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr comment failed: {result.stderr}")
    print(f"✓ Commented on {repo}#{number}")

def gh_close(repo: str, number: int, comment_body: Optional[str] = None) -> None:
    """Close a PR with optional comment."""
    if comment_body:
        gh_comment(repo, number, comment_body)
    cmd = ["gh", "pr", "close", str(number), "-R", repo]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr close failed: {result.stderr}")
    print(f"✓ Closed {repo}#{number}")

# ── Opencode Server Management ───────────────────────────────────────────────

def start_opencode_server() -> subprocess.Popen:
    """Start opencode serve in background."""
    print(f"→ Starting opencode serve on port {OPENCODE_PORT}...")
    process = subprocess.Popen(
        ["opencode", "serve", "--port", str(OPENCODE_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Wait for server to be ready
    for _ in range(30):
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://localhost:{OPENCODE_PORT}/health"],
                capture_output=True, text=True, timeout=2
            )
            if result.stdout.strip() == "200":
                print("✓ opencode server ready")
                return process
        except Exception:
            pass
        time.sleep(1)
    print("⚠ opencode server may not be ready, continuing anyway")
    return process

def stop_opencode_server(process: subprocess.Popen) -> None:
    """Stop opencode serve."""
    print("→ Stopping opencode server...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    print("✓ opencode server stopped")

# ── Prompt Engineering ──────────────────────────────────────────────────────

def build_prompt(ctx: PRContext) -> str:
    """Build a structured prompt for opencode analysis."""
    
    comments_summary = "\n".join([
        f"- [{c.get('author', {}).get('login', 'unknown')}]: {c.get('body', '')[:200]}"
        for c in ctx.comments[:5]  # Last 5 comments
    ]) if ctx.comments else "No comments"
    
    reviews_summary = "\n".join([
        f"- [{r.get('author', {}).get('login', 'unknown')}]: {r.get('state', 'unknown')}"
        for r in ctx.reviews[:3]
    ]) if ctx.reviews else "No reviews"
    
    prompt = f"""You are an OSS contribution agent. Analyze this PR and decide the next action.

## PR Context
- Repository: {ctx.repo}
- PR #{ctx.number}: {ctx.title}
- Author: {ctx.author}
- State: {ctx.state}
- Mergeable: {ctx.mergeable}
- CI Status: {ctx.ci_status}
- Files changed: {ctx.files_changed} (+{ctx.additions}/-{ctx.deletions})
- Stale days: {ctx.stale_days}
- Tier: {ctx.tier}
- Last updated: {ctx.last_updated}

## Comments
{comments_summary}

## Reviews
{reviews_summary}

## Safety Rules (NEVER violate)
1. NEVER force-push to upstream PR branches
2. NEVER bulk-act on PRs (one at a time)
3. NEVER post a comment without reading the PR first
4. NEVER re-open or un-close a PR
5. NEVER forge DCO sign-off or CLA confirmation
6. If maintainer says "stop" or closes the PR, you stop

## Decision Options
- "nudge": Post a polite status-update comment. Use when PR is mergeable, CI green, but stale.
- "fix": Push a code fix to the fork. Use when CI is red and you can identify the root cause.
- "close": Close the PR. Use when superseded by upstream, archived repo, or maintainer asked.
- "skip": Do nothing. Use when recently nudged, waiting for maintainer reply, or ambiguous.
- "defer": Defer to human. Use when needs rebase, needs CLA, or you're unsure.

## Output Format
Return ONLY a JSON object with this exact schema:
{{
  "action": "nudge|fix|close|skip|defer",
  "reason": "string explaining why",
  "confidence": 1-10,
  "comment_body": "string (for nudge/close actions)",
  "requires_human": true|false
}}

Analyze now and return the JSON.
"""
    return prompt

# ── Opencode Execution ─────────────────────────────────────────────────────

def run_opencode(prompt: str) -> str:
    """Run opencode in headless mode and return the model text output."""
    cmd = [
        "opencode", "run",
        "--attach", OPENCODE_ATTACH,
        "--format", "json",
        "--model", OPENCODE_MODEL,
        prompt
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    
    if result.returncode != 0:
        raise RuntimeError(f"opencode run failed: {result.stderr}")
    
    # Parse JSON events from output - collect text from "text" events
    output_lines = result.stdout.strip().split("\n")
    text_parts = []
    for line in output_lines:
        try:
            event = json.loads(line)
            if event.get("type") == "text":
                text = event.get("part", {}).get("text", "")
                if text:
                    text_parts.append(text)
        except json.JSONDecodeError:
            continue
    
    if text_parts:
        return "\n".join(text_parts)
    
    # Fallback: return raw output
    return result.stdout

def parse_decision(output: str) -> Decision:
    """Extract JSON decision from opencode output."""
    # Find the last JSON block in the output
    json_blocks = re.findall(r'\{.*\}', output, re.DOTALL)
    if not json_blocks:
        raise ValueError("No JSON found in opencode output")
    
    # Try each block from last to first
    for block in reversed(json_blocks):
        try:
            data = json.loads(block)
            # Validate it has the expected fields
            if "action" in data:
                return Decision(
                    action=data.get("action", "skip"),
                    reason=data.get("reason", "parse error"),
                    confidence=data.get("confidence", 1),
                    comment_body=data.get("comment_body"),
                    requires_human=data.get("requires_human", False)
                )
        except json.JSONDecodeError:
            continue
    
    raise ValueError("Invalid JSON in opencode output")

# ── Execution ───────────────────────────────────────────────────────────────

def execute_decision(ctx: PRContext, decision: Decision, dry_run: bool) -> None:
    """Execute the decision on a PR."""
    
    print(f"\n{'='*60}")
    print(f"Decision: {decision.action} (confidence: {decision.confidence}/10)")
    print(f"Reason: {decision.reason}")
    print(f"Requires human: {decision.requires_human}")
    print(f"{'='*60}\n")
    
    if dry_run:
        print("[DRY RUN] No action taken")
        return
    
    if decision.requires_human:
        print("⚠ Human approval required. Creating issue...")
        # In a real implementation, create a GitHub Issue in oss-tracker
        return
    
    if decision.action == "nudge":
        if not decision.comment_body:
            print("⚠ No comment body for nudge, skipping")
            return
        gh_comment(ctx.repo, ctx.number, decision.comment_body)
    
    elif decision.action == "close":
        if not decision.comment_body:
            print("⚠ No comment body for close, skipping")
            return
        gh_close(ctx.repo, ctx.number, decision.comment_body)
    
    elif decision.action == "fix":
        print("⚠ Fix action requires manual implementation. Deferring to human.")
        # In future: checkout fork, apply patch, push
        return
    
    elif decision.action == "skip":
        print("→ Skipped")
    
    elif decision.action == "defer":
        print("→ Deferred to human")
    
    else:
        print(f"⚠ Unknown action: {decision.action}")

def analyze_pr(repo: str, number: int, dry_run: bool) -> Decision:
    """Analyze a single PR and return the decision."""
    print(f"\n🔍 Analyzing {repo}#{number}...")
    
    # Fetch PR data
    pr_data = gh_pr_view(repo, number)
    
    # Determine CI status
    checks = pr_data.get("statusCheckRollup", [])
    failed = sum(1 for c in checks if c.get("conclusion") in ["FAILURE", "ERROR"])
    pending = sum(1 for c in checks if c.get("status") == "IN_PROGRESS")
    total = len(checks)
    
    if failed > 0:
        ci_status = f"{failed}/{total} failing"
    elif pending > 0:
        ci_status = f"{pending}/{total} pending"
    elif total > 0:
        ci_status = f"{total}/{total} passing"
    else:
        ci_status = "no checks"
    
    ctx = PRContext(
        repo=repo,
        number=number,
        title=pr_data.get("title", "unknown"),
        author=pr_data.get("author", {}).get("login", "unknown"),
        state=pr_data.get("state", "unknown"),
        mergeable=pr_data.get("mergeable"),
        ci_status=ci_status,
        comments=pr_data.get("comments", []),
        reviews=pr_data.get("reviews", []),
        files_changed=len(pr_data.get("files", [])),
        additions=pr_data.get("additions", 0),
        deletions=pr_data.get("deletions", 0),
        stale_days=0,  # Calculated from updatedAt
        tier="unknown",
        last_updated=pr_data.get("updatedAt", "")
    )
    
    # Build and run prompt
    prompt = build_prompt(ctx)
    
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"  → opencode attempt {attempt}/{MAX_ATTEMPTS}...")
            output = run_opencode(prompt)
            decision = parse_decision(output)
            print(f"  ✓ Decision parsed: {decision.action}")
            return decision
        except Exception as e:
            print(f"  ✗ Attempt {attempt} failed: {e}")
            if attempt == MAX_ATTEMPTS:
                return Decision(
                    action="skip",
                    reason=f"Failed to get decision after {MAX_ATTEMPTS} attempts: {e}",
                    confidence=0,
                    requires_human=True
                )
            time.sleep(2)

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OSS Agent - Opencode Runner")
    parser.add_argument("repo", help="Owner/repo format")
    parser.add_argument("number", type=int, help="PR number")
    parser.add_argument("--dry-run", action="store_true", help="No writes")
    args = parser.parse_args()
    
    server = None
    try:
        server = start_opencode_server()
        decision = analyze_pr(args.repo, args.number, args.dry_run)
        
        # For single-PR mode, we just print the decision
        # In batch mode, the caller (daily-batch.sh) handles execution
        print(f"\n📋 Final Decision:")
        print(json.dumps(asdict(decision), indent=2))
        
    finally:
        if server:
            stop_opencode_server(server)

if __name__ == "__main__":
    main()
