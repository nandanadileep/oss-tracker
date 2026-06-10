#!/usr/bin/env python3
"""Candidate Discovery engine.

Finds fresh high-activity repositories and candidate issues, scores them, and
writes to candidates.json. Never opens PRs. Discovery feeds New Contributor.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
CANDIDATES_FILE = STATE_DIR / "candidates.json"
ACTIONS_FILE = STATE_DIR / "actions.jsonl"
PROGRESS_FILE = STATE_DIR / "progress.md"
PREFLIGHT = ROOT / ".oss-harness" / "scanners" / "repo-preflight.py"

OPENCODE_MODEL = "zen/big-pickle"
MAX_MODEL_ATTEMPTS = 3
DEFAULT_DISCOVERY_LIMIT = 10

# Issue labels we prefer
PREFERRED_LABELS = {"good first issue", "help wanted", "bug", "regression", "test", "docs"}


@dataclass
class RepoCandidate:
    name_with_owner: str
    default_branch: str
    stars: int
    language: str
    last_pushed_at: str
    open_issues_count: int
    license: str
    archived: bool
    ai_policy_status: str
    existing_open_prs_by_hari: int
    repo_score: float
    status: str
    discovery_source: str
    discovered_at: str


@dataclass
class IssueCandidate:
    repo: str
    issue_number: int
    title: str
    labels: list[str]
    body_summary: str
    confidence: int
    repo_score: float
    issue_score: float
    total_score: float
    estimated_scope: str
    requires_domain_knowledge: bool
    has_reproduction: bool
    has_tests_hint: bool
    maintainer_activity_score: float
    dedupe_key: str
    status: str
    selected_at: str | None
    daily_target_mode: str
    issue_comment_policy: str


@dataclass
class DiscoveryRecord:
    type: str
    target: str
    reason: str
    confidence: int
    created_at: str
    workflow_run_id: str | None
    dry_run: bool


@dataclass
class DiscoverySource:
    type: str
    query: str
    schedule: str
    last_run_at: str
    repos_found: int
    repos_added: int
    issues_added: int
    deduped_count: int
    blocked_count: int


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


def append_action(record: DiscoveryRecord) -> None:
    ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ACTIONS_FILE.open("a") as f:
        f.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def gh_json(args: list[str], *, timeout: int = 60) -> Any:
    result = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh command failed")
    return json.loads(result.stdout or "null")


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


def search_repos(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search GitHub for repositories using gh search repos."""
    try:
        results = gh_json(
            ["search", "repos", query, "--limit", str(limit), "--sort", "updated", "--json", "fullName,defaultBranch,stargazersCount,language,pushedAt,openIssuesCount,licenseInfo"]
        )
    except Exception as exc:
        print(f"  repo search failed: {exc}")
        return []
    return results or []


def search_issues(repo: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search issues in a specific repo."""
    try:
        results = gh_json(
            ["search", "issues", query, "repo:" + repo, "--limit", str(limit), "--sort", "updated", "--json", "number,title,labels,body,updatedAt,createdAt"]
        )
    except Exception as exc:
        print(f"  issue search failed for {repo}: {exc}")
        return []
    return results or []


def count_hari_prs(repo: str, hari_login: str) -> int:
    """Count open PRs by Hari in a repo."""
    try:
        results = gh_json(
            ["search", "prs", f"repo:{repo} author:{hari_login} is:open", "--limit", "100", "--json", "number"]
        )
    except Exception:
        return 0
    return len(results or [])


def score_repo(repo_data: dict[str, Any]) -> float:
    """Score a repo candidate. Higher is better."""
    stars = repo_data.get("stargazersCount", 0) or 0
    open_issues = repo_data.get("openIssuesCount", 0) or 0
    last_pushed = repo_data.get("pushedAt", "")
    
    # Base score from stars (log scale)
    score = min(5.0, max(1.0, stars / 1000))
    
    # Bonus for recent activity
    if last_pushed:
        try:
            pushed_dt = datetime.fromisoformat(last_pushed.replace("Z", "+00:00"))
            days_since_push = (utcnow() - pushed_dt).days
            if days_since_push < 7:
                score += 2.0
            elif days_since_push < 30:
                score += 1.0
        except ValueError:
            pass
    
    # Penalty for too many open issues (overwhelmed maintainers)
    if open_issues > 1000:
        score -= 1.0
    elif open_issues > 500:
        score -= 0.5
    
    return max(0.0, score)


def score_issue(issue_data: dict[str, Any], repo_score: float) -> float:
    """Score an issue candidate."""
    labels = issue_data.get("labels", []) or []
    label_names = {str(l.get("name", "")).lower() for l in labels}
    
    score = 0.0
    
    # Prefer certain labels
    if "good first issue" in label_names:
        score += 3.0
    if "help wanted" in label_names:
        score += 2.0
    if "bug" in label_names:
        score += 2.0
    if "regression" in label_names:
        score += 2.5
    if "test" in label_names:
        score += 1.5
    if "docs" in label_names:
        score += 1.0
    
    # Prefer issues with recent activity
    updated_at = issue_data.get("updatedAt", "")
    if updated_at:
        try:
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            days_since_update = (utcnow() - updated_dt).days
            if days_since_update < 7:
                score += 1.5
            elif days_since_update < 30:
                score += 0.5
        except ValueError:
            pass
    
    # Prefer newer issues
    created_at = issue_data.get("createdAt", "")
    if created_at:
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            days_since_create = (utcnow() - created_dt).days
            if days_since_create < 30:
                score += 1.0
            elif days_since_create > 180:
                score -= 0.5
        except ValueError:
            pass
    
    # Combine with repo score
    total = score + repo_score * 0.5
    return max(0.0, total)


def estimate_scope(issue_data: dict[str, Any]) -> str:
    """Estimate scope of an issue."""
    body = (issue_data.get("body", "") or "").lower()
    title = (issue_data.get("title", "") or "").lower()
    combined = title + " " + body
    
    # Tiny: typo, doc fix, single line
    if any(k in combined for k in ["typo", "doc fix", "spelling", "grammar", "one-line", "single-line"]):
        return "tiny"
    
    # Small: obvious bug with clear fix
    if any(k in combined for k in ["bug", "fix", "broken", "error", "crash"]):
        return "small"
    
    # Medium: feature request, enhancement, test addition
    if any(k in combined for k in ["feature", "enhancement", "add support", "implement"]):
        return "medium"
    
    return "small"


def has_reproduction(issue_data: dict[str, Any]) -> bool:
    body = (issue_data.get("body", "") or "").lower()
    return any(k in body for k in ["reproduction", "reproduce", "steps to reproduce", "minimal example", "mre"])


def has_tests_hint(issue_data: dict[str, Any]) -> bool:
    body = (issue_data.get("body", "") or "").lower()
    return any(k in body for k in ["test", "pytest", "unittest", "coverage"])


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


def build_issue_summary_prompt(issue_data: dict[str, Any]) -> str:
    title = issue_data.get("title", "")
    body = (issue_data.get("body", "") or "")[:2000]
    return f"""Summarize this GitHub issue in one sentence.

Issue: {title}

Body:
{body}

Return only JSON:
{{
  "summary": "one sentence summary",
  "requires_domain_knowledge": true or false,
  "maintainer_activity_score": 1-10
}}
"""


def summarize_issue(issue_data: dict[str, Any]) -> tuple[str, bool, float]:
    """Use opencode to summarize issue and estimate domain knowledge need."""
    for attempt in range(1, MAX_MODEL_ATTEMPTS + 1):
        try:
            output = run_opencode(build_issue_summary_prompt(issue_data))
            matches = re.findall(r"\{.*\}", output, re.DOTALL)
            for block in reversed(matches):
                try:
                    data = json.loads(block)
                    return (
                        str(data.get("summary", "") or ""),
                        bool(data.get("requires_domain_knowledge", False)),
                        float(data.get("maintainer_activity_score", 5) or 5),
                    )
                except json.JSONDecodeError:
                    continue
        except Exception as exc:
            print(f"  issue summary attempt {attempt} failed: {exc}")
            time.sleep(1)
    return "", False, 5.0


def load_candidates() -> dict[str, Any]:
    data = load_json(CANDIDATES_FILE, {"_version": "0.1.0", "candidates": []})
    if isinstance(data, list):
        data = {"_version": "0.1.0", "candidates": data}
    data.setdefault("candidates", [])
    return data


def dedupe_key(repo: str, issue_number: int) -> str:
    return f"{repo}#{issue_number}"


def find_existing_candidate(data: dict[str, Any], dedupe_key: str) -> dict[str, Any] | None:
    for candidate in data.get("candidates", []):
        if candidate.get("dedupe_key") == dedupe_key:
            return candidate
    return None


def discover_repos(query: str, limit: int, hari_login: str) -> list[RepoCandidate]:
    """Discover and score repositories."""
    print(f"Discovering repos with query: {query}")
    results = search_repos(query, limit=limit)
    candidates: list[RepoCandidate] = []
    
    for repo_data in results:
        repo_name = repo_data.get("fullName", "")
        if not repo_name:
            continue
        
        print(f"  checking {repo_name}")
        
        # Preflight
        ok, msg = run_preflight(repo_name)
        if not ok:
            print(f"    preflight blocked: {msg}")
            continue
        
        # Count existing PRs
        hari_prs = count_hari_prs(repo_name, hari_login)
        max_open = load_json(CONFIG_FILE, {}).get("limits", {}).get("max_open_per_repo", 30)
        if hari_prs >= max_open:
            print(f"    skipped: {hari_prs} open PRs >= limit {max_open}")
            continue
        
        score = score_repo(repo_data)
        
        candidates.append(RepoCandidate(
            name_with_owner=repo_name,
            default_branch=repo_data.get("defaultBranch", "main"),
            stars=repo_data.get("stargazersCount", 0) or 0,
            language=repo_data.get("language", "") or "",
            last_pushed_at=repo_data.get("pushedAt", "") or "",
            open_issues_count=repo_data.get("openIssuesCount", 0) or 0,
            license=repo_data.get("licenseInfo", {}).get("name", "") or "",
            archived=False,
            ai_policy_status="unknown",
            existing_open_prs_by_hari=hari_prs,
            repo_score=score,
            status="eligible",
            discovery_source="github_search",
            discovered_at=iso_now(),
        ))
    
    return candidates


def discover_issues_for_repo(repo_candidate: RepoCandidate, limit: int) -> list[IssueCandidate]:
    """Find and score candidate issues in a repo."""
    print(f"  discovering issues in {repo_candidate.name_with_owner}")
    
    # Build search query with preferred labels
    label_queries = []
    for label in PREFERRED_LABELS:
        label_queries.append(f"label:\"{label}\"")
    
    # Search for open issues
    query = f"repo:{repo_candidate.name_with_owner} is:issue is:open"
    
    results = search_issues(repo_candidate.name_with_owner, query, limit=limit)
    
    candidates: list[IssueCandidate] = []
    for issue_data in results:
        issue_number = issue_data.get("number", 0)
        if not issue_number:
            continue
        
        dedupe = dedupe_key(repo_candidate.name_with_owner, issue_number)
        
        labels = [str(l.get("name", "")) for l in (issue_data.get("labels", []) or [])]
        
        # Score
        issue_score = score_issue(issue_data, repo_candidate.repo_score)
        scope = estimate_scope(issue_data)
        
        # Get summary from model
        summary, needs_domain, maintainer_score = summarize_issue(issue_data)
        
        candidates.append(IssueCandidate(
            repo=repo_candidate.name_with_owner,
            issue_number=issue_number,
            title=issue_data.get("title", ""),
            labels=labels,
            body_summary=summary,
            confidence=min(10, max(1, int(issue_score))),
            repo_score=repo_candidate.repo_score,
            issue_score=issue_score,
            total_score=issue_score + repo_candidate.repo_score,
            estimated_scope=scope,
            requires_domain_knowledge=needs_domain,
            has_reproduction=has_reproduction(issue_data),
            has_tests_hint=has_tests_hint(issue_data),
            maintainer_activity_score=maintainer_score,
            dedupe_key=dedupe,
            status="new",
            selected_at=None,
            daily_target_mode="adaptive_1_to_5",
            issue_comment_policy="comment_or_claim_then_open_pr",
        ))
    
    return candidates


def run_discovery(
    repo_queries: list[str],
    repo_limit: int,
    issues_per_repo: int,
    hari_login: str,
    dry_run: bool,
) -> list[DiscoveryRecord]:
    """Run full discovery pipeline."""
    records: list[DiscoveryRecord] = []
    workflow_run_id = os.environ.get("GITHUB_RUN_ID")
    
    candidates_data = load_candidates()
    existing_keys = {c.get("dedupe_key", "") for c in candidates_data.get("candidates", [])}
    
    repos_added = 0
    issues_added = 0
    deduped = 0
    blocked = 0
    
    for query in repo_queries:
        print(f"Query: {query}")
        repo_candidates = discover_repos(query, repo_limit, hari_login)
        
        for repo_candidate in repo_candidates:
            if repo_candidate.name_with_owner in existing_keys:
                print(f"    repo already known: {repo_candidate.name_with_owner}")
                deduped += 1
                continue
            
            # Add repo to candidates as a marker
            repo_marker = {
                "type": "repo",
                "name_with_owner": repo_candidate.name_with_owner,
                "dedupe_key": repo_candidate.name_with_owner,
                "repo_score": repo_candidate.repo_score,
                "status": repo_candidate.status,
                "discovered_at": repo_candidate.discovered_at,
                "discovery_source": repo_candidate.discovery_source,
                "stars": repo_candidate.stars,
                "language": repo_candidate.language,
                "open_issues_count": repo_candidate.open_issues_count,
                "existing_open_prs_by_hari": repo_candidate.existing_open_prs_by_hari,
            }
            candidates_data["candidates"].append(repo_marker)
            existing_keys.add(repo_candidate.name_with_owner)
            repos_added += 1
            
            # Discover issues
            issue_candidates = discover_issues_for_repo(repo_candidate, issues_per_repo)
            
            for issue in issue_candidates:
                if issue.dedupe_key in existing_keys:
                    print(f"    issue already known: {issue.dedupe_key}")
                    deduped += 1
                    continue
                
                candidate_dict = {
                    "type": "issue",
                    "repo": issue.repo,
                    "issue_number": issue.issue_number,
                    "title": issue.title,
                    "labels": issue.labels,
                    "body_summary": issue.body_summary,
                    "confidence": issue.confidence,
                    "repo_score": issue.repo_score,
                    "issue_score": issue.issue_score,
                    "total_score": issue.total_score,
                    "estimated_scope": issue.estimated_scope,
                    "requires_domain_knowledge": issue.requires_domain_knowledge,
                    "has_reproduction": issue.has_reproduction,
                    "has_tests_hint": issue.has_tests_hint,
                    "maintainer_activity_score": issue.maintainer_activity_score,
                    "dedupe_key": issue.dedupe_key,
                    "status": issue.status,
                    "selected_at": issue.selected_at,
                    "daily_target_mode": issue.daily_target_mode,
                    "issue_comment_policy": issue.issue_comment_policy,
                    "discovered_at": iso_now(),
                }
                candidates_data["candidates"].append(candidate_dict)
                existing_keys.add(issue.dedupe_key)
                issues_added += 1
                
                record = DiscoveryRecord(
                    type="discover",
                    target=issue.dedupe_key,
                    reason=f"score={issue.total_score:.1f} scope={issue.estimated_scope}",
                    confidence=issue.confidence,
                    created_at=iso_now(),
                    workflow_run_id=workflow_run_id,
                    dry_run=dry_run,
                )
                records.append(record)
                if not dry_run:
                    append_action(record)
                
                print(f"    added: {issue.dedupe_key} score={issue.total_score:.1f}")
            
            time.sleep(1)  # Rate limit safety
        
        time.sleep(2)  # Rate limit safety between queries
    
    # Update discovery source metadata
    discovery_meta = {
        "type": "github_search",
        "query": "; ".join(repo_queries),
        "schedule": "weekly",
        "last_run_at": iso_now(),
        "repos_found": len(repo_candidates),
        "repos_added": repos_added,
        "issues_added": issues_added,
        "deduped_count": deduped,
        "blocked_count": blocked,
    }
    candidates_data.setdefault("discovery_sources", []).append(discovery_meta)
    candidates_data["_updated"] = iso_now()
    
    if not dry_run:
        write_json(CANDIDATES_FILE, candidates_data)
    
    # Progress summary
    summary_lines = [
        "",
        f"## {utcnow().date().isoformat()} - Candidate Discovery",
        "",
        f"- Repos added: {repos_added}",
        f"- Issues added: {issues_added}",
        f"- Deduped: {deduped}",
        f"- Dry run: {dry_run}",
        "",
        "New candidates:",
    ]
    for record in records:
        summary_lines.append(f"  - {record.target}: {record.reason}")
    
    with PROGRESS_FILE.open("a") as f:
        f.write("\n".join(summary_lines) + "\n")
    
    print(f"Discovery complete: {repos_added} repos, {issues_added} issues, {deduped} deduped")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate Discovery")
    parser.add_argument("--queries", nargs="+", default=["stars:>1000 language:python pushed:>2026-05-01"])
    parser.add_argument("--repo-limit", type=int, default=10)
    parser.add_argument("--issues-per-repo", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    config = load_json(CONFIG_FILE, {})
    hari_login = config.get("user", {}).get("login", "Mr-Neutr0n")
    
    records = run_discovery(
        args.queries,
        args.repo_limit,
        args.issues_per_repo,
        hari_login,
        args.dry_run,
    )
    
    print(f"Discovered {len(records)} new candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
