"""Discovery: find (repo, issue) candidates, screen deterministically, queue.

Sources: gh issue search across label feeds + the existing candidates.json
repo seeds. Screening is signal-based (no LLM needed): labels, age, assignee,
linked PRs, lock state. Output: candidate events in the ledger + a queue view.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import gh as github
from ..domain import Candidate, CandidateState
from ..events import Ev
from ..ids import age, iso_now
from . import harness_run

LABEL_QUERIES = ("good first issue", "help wanted")
REJECT_LABELS = {"wontfix", "invalid", "duplicate", "question", "discussion", "needs triage"}
MIN_AGE_DAYS, MAX_AGE_DAYS = 7, 540  # racing humans < 1wk; likely obsolete > 18mo


def screen(issue: dict, cfg) -> tuple[str, float, str]:
    """Returns (verdict, score, reason). verdict: queue | reject."""
    labels = {l["name"].lower() for l in issue.get("labels", [])}
    if labels & REJECT_LABELS:
        return "reject", 0, f"label:{(labels & REJECT_LABELS).pop()}"
    if issue.get("assignees"):
        return "reject", 0, "assigned"
    if issue.get("isLocked") or issue.get("locked"):
        return "reject", 0, "locked"
    if issue.get("isPullRequest") or "pull_request" in issue:
        return "reject", 0, "is a PR"
    days = age(issue.get("createdAt") or issue.get("created_at")).days
    if days < MIN_AGE_DAYS:
        return "reject", 0, "too fresh (racing humans)"
    if days > MAX_AGE_DAYS:
        return "reject", 0, "stale (>18 months)"
    owner = (issue.get("repository") or {}).get("nameWithOwner", "").split("/")[0]
    if owner in cfg.exclude_owners:
        return "reject", 0, "excluded owner"
    score = 5.0
    if "good first issue" in labels:
        score += 3
    if "help wanted" in labels:
        score += 2
    if "bug" in labels:
        score += 1
    score -= min(2.0, days / 365)  # mild freshness preference
    return "queue", score, ""


def tier_for(score: float) -> str:
    return "A" if score >= 8 else "B" if score >= 6 else "C"


def search_issues(label: str, limit: int) -> list[dict]:
    return github.gh_json([
        "search", "issues", "--label", label,
        "--state", "open", "--language", "python",
        "--sort", "updated", "--limit", str(limit),
        "--json", "title,number,labels,createdAt,isLocked,isPullRequest,assignees,repository",
    ]) or []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-new", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    with harness_run("discover", dry_run=args.dry_run) as ctx:
        seen = ctx.ledger.subjects_with(Ev.CANDIDATE_DISCOVERED)
        # mark expirations first (TTL fold, §4)
        ttl = ctx.cfg.limits.candidate_ttl_days
        queued_now = _queued(ctx)
        for cand in queued_now:
            if cand.expired_by(ttl):
                ctx.ledger.append(Ev.CANDIDATE_EXPIRED, cand.subject, ttl_days=ttl)

        added = 0
        for q in LABEL_QUERIES:
            if added >= args.max_new:
                break
            try:
                results = search_issues(q, 30)
            except github.GhError as e:
                print(f"[discover] search failed for {q}: {e}", file=sys.stderr)
                continue
            for issue in results:
                repo = (issue.get("repository") or {}).get("nameWithOwner", "")
                if not repo or added >= args.max_new:
                    continue
                subject = f"{repo}#{issue['number']}"
                if subject in seen:
                    continue
                seen.add(subject)
                verdict, score, reason = screen(issue, ctx.cfg)
                ctx.ledger.append(Ev.CANDIDATE_DISCOVERED, subject,
                                  source=q, title=issue.get("title", "")[:150])
                if verdict == "queue":
                    ctx.ledger.append(Ev.CANDIDATE_SCREENED, subject,
                                      score=score, tier=tier_for(score))
                    added += 1
                else:
                    ctx.ledger.append(Ev.CANDIDATE_REJECTED, subject, reason=reason)

        queue = _queued(ctx)
        _write_view(ctx, queue)
        print(f"[discover] queued {added} new; queue depth {len(queue)}")
    return 0


def _queued(ctx) -> list[Candidate]:
    """Fold candidate events into the current queue (screened minus consumed)."""
    state: dict[str, Candidate] = {}
    for ev in ctx.ledger.events():
        subj = ev.subject
        if ev.event == Ev.CANDIDATE_DISCOVERED:
            repo, _, num = subj.partition("#")
            if num.isdigit():
                state[subj] = Candidate(repo=repo, issue_number=int(num),
                                        discovered_at=ev.at,
                                        source=ev.data.get("source", ""),
                                        issue_title=ev.data.get("title", ""))
        elif subj in state:
            c = state[subj]
            if ev.event == Ev.CANDIDATE_SCREENED:
                state[subj] = Candidate(**{**c.__dict__, "state": CandidateState.QUEUED,
                                           "score": ev.data.get("score", 0),
                                           "tier": ev.data.get("tier", "C")})
            elif ev.event in (Ev.CANDIDATE_REJECTED, Ev.CANDIDATE_EXPIRED,
                              Ev.CONTRIBUTION_STARTED, Ev.CANDIDATE_CLAIMED):
                state.pop(subj, None)
    return sorted([c for c in state.values() if c.state is CandidateState.QUEUED],
                  key=lambda c: (-c.score, c.discovered_at))


def _write_view(ctx, queue: list[Candidate]) -> None:
    view = {
        "_version": 2, "_generated": iso_now(),
        "_comment": "Materialized view — rebuilt from ledger.jsonl every run. Do not hand-edit.",
        "queue": [{"subject": c.subject, "tier": c.tier, "score": c.score,
                   "title": c.issue_title, "discovered_at": c.discovered_at}
                  for c in queue],
    }
    path = Path(ctx.cfg.state_dir) / "queue.json"
    path.write_text(json.dumps(view, indent=1) + "\n")


if __name__ == "__main__":
    sys.exit(main())
