"""Stewardship: tend existing open PRs — classify, then run the playbook.

Reason classification and playbooks per DOMAIN_MODEL.md §6. Maintainer replies
outrank everything; nudges are governed by NudgePolicy; archived repos get an
honest close; CLA bots escalate; pending CI is left alone.
"""

from __future__ import annotations

import argparse
import sys

from .. import gh as github
from ..domain import (CaseReason, Escalation, EscalationReason, NudgePolicy,
                      StewardCase, stop_signal_in)
from ..events import Ev
from ..ids import age
from . import harness_run

STALE_DAYS = 30
CLA_MARKERS = ("cla", "contributor license", "license/cla", "easycla", "cla-bot")


def classify(case_repo: str, pr: dict, our_login: str) -> StewardCase:
    """Order matters: terminal conditions first, then actionable, then stale."""
    number = pr["number"]

    if pr.get("mergedAt") or pr.get("state") == "MERGED":
        return StewardCase(case_repo, number, CaseReason.HEALTHY, "merged")
    if pr.get("closed") or pr.get("state") == "CLOSED":
        return StewardCase(case_repo, number, CaseReason.HEALTHY, "closed")

    facts = github.repo_facts(case_repo)
    if facts.archived:
        return StewardCase(case_repo, number, CaseReason.REPO_ARCHIVED)

    # CLA bot blocking?
    for check in pr.get("statusCheckRollup") or []:
        name = (check.get("name") or check.get("context") or "").lower()
        state = (check.get("conclusion") or check.get("state") or "").upper()
        if any(m in name for m in CLA_MARKERS) and state in ("FAILURE", "ACTION_REQUIRED", "ERROR"):
            return StewardCase(case_repo, number, CaseReason.CLA_BLOCKING, name)

    # newest non-self comment/review wins
    interactions = [(c.get("createdAt") or c.get("submittedAt") or "",
                     (c.get("author") or {}).get("login", ""),
                     c.get("body") or "")
                    for c in (pr.get("comments") or []) + (pr.get("reviews") or [])]
    foreign = [i for i in sorted(interactions) if i[1] and i[1] != our_login
               and not i[1].endswith("[bot]")]
    last_activity_at = max((i[0] for i in interactions), default="")
    if foreign:
        at, author, body = foreign[-1]
        ours_after = [i for i in interactions if i[1] == our_login and i[0] > at]
        if not ours_after:
            return StewardCase(case_repo, number, CaseReason.MAINTAINER_REPLIED,
                               f"{author}: {body[:200]}")

    if pr.get("mergeable") == "CONFLICTING":
        return StewardCase(case_repo, number, CaseReason.MERGE_CONFLICT)

    ci = github.classify_ci(pr.get("statusCheckRollup"))
    if ci == "failed":
        return StewardCase(case_repo, number, CaseReason.CI_FAILED)
    if ci == "pending_approval":
        return StewardCase(case_repo, number, CaseReason.HEALTHY,
                           "CI awaiting maintainer approval — clock paused")

    ref = last_activity_at or pr.get("updatedAt") or ""
    if ref and age(ref).days >= STALE_DAYS and facts.pushed_at and age(facts.pushed_at).days < 365:
        return StewardCase(case_repo, number, CaseReason.STALE_NO_RESPONSE)
    return StewardCase(case_repo, number, CaseReason.HEALTHY)


def act(ctx, case: StewardCase) -> str:
    cfg, ledger, ex = ctx.cfg, ctx.ledger, ctx.executor
    ledger.append(Ev.CASE_REVIEWED, case.subject, reason=case.reason.value,
                  detail=case.detail[:200])

    if case.reason is CaseReason.HEALTHY:
        return f"healthy ({case.detail})" if case.detail else "healthy"

    if case.reason is CaseReason.MAINTAINER_REPLIED:
        ledger.append(Ev.MAINTAINER_INTERACTION, case.subject,
                      sentiment="stop_signal" if stop_signal_in(case.detail) else "neutral",
                      detail=case.detail[:200])
        if stop_signal_in(case.detail):
            a = ex.propose("withdraw_pr", case.repo, case.pr_number,
                           rationale="maintainer stop signal",
                           reason="maintainer_request", evidence=case.detail[:200],
                           comment="Understood, closing. Sorry for the noise, "
                                   "and thanks for maintaining this project.")
            r = ex.execute(a)
            if r.outcome == "ok":
                ledger.append(Ev.PR_WITHDRAWN, case.subject, reason="stop_signal")
            return f"withdrew per stop signal ({r.outcome})"
        # substantive replies need judgment → escalate rather than auto-answer
        ex.escalate(Escalation(EscalationReason.SOCIAL, case.subject,
                               f"Maintainer replied on {case.subject}: {case.detail[:300]} "
                               f"— review and reply, or authorize an agent reply."))
        return "escalated: maintainer reply needs human review"

    if case.reason is CaseReason.REPO_ARCHIVED:
        a = ex.propose("close_pr", case.repo, case.pr_number,
                       rationale="repo archived", reason="repo_archived",
                       comment="Closing: the repository was archived upstream, "
                               "so this change is no longer needed.")
        r = ex.execute(a)
        return f"closed (archived) ({r.outcome})"

    if case.reason is CaseReason.CLA_BLOCKING:
        ex.escalate(Escalation(EscalationReason.CLA_SIGNATURE, case.repo.split("/")[0],
                               f"CLA check `{case.detail}` is blocking {case.subject}. "
                               f"Sign once for the org; the harness retries after."))
        return "escalated: CLA signature needed"

    if case.reason is CaseReason.STALE_NO_RESPONSE:
        policy = NudgePolicy(cfg.limits.nudge_interval_days, cfg.limits.nudge_lifetime_cap)
        nudges = [e for e in ledger.events() if e.event == Ev.NUDGE_POSTED]
        ok, why = policy.may_nudge(case.subject, nudges)
        if not ok:
            if "lifetime" in why:
                ledger.append(Ev.CASE_DORMANT, case.subject, reason=why)
            return f"no nudge: {why}"
        body = ("Quick status: this still applies cleanly to the latest default branch "
                "and CI is green on my side. Happy to update for any feedback, and just "
                "as happy to close if the project has moved on.")
        a = ex.propose("post_comment", case.repo, case.pr_number,
                       rationale="stale >30d, nudge within policy", body=body, on="pr")
        r = ex.execute(a)
        if r.outcome == "ok":
            ledger.append(Ev.NUDGE_POSTED, case.subject, body=body)
        return f"nudged ({r.outcome})"

    # conflicts and CI failures need a workspace + model; out of scope for the
    # read-mostly steward pass — surface them so contribute-mode picks them up.
    ledger.append(Ev.CI_OBSERVED, case.subject, status=case.reason.value)
    return f"flagged: {case.reason.value} (fix pass handles separately)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    with harness_run("steward", dry_run=args.dry_run) as ctx:
        prs = github.list_my_open_prs(ctx.cfg.login, args.batch_size * 2)
        # oldest-updated first: the most neglected PRs get attention first
        prs.sort(key=lambda p: p.get("updatedAt", ""))
        handled = 0
        for item in prs:
            if handled >= args.batch_size:
                break
            if github.rate_remaining() < 0.15:
                print("[steward] API quota low; ending batch early")
                break
            repo = (item.get("repository") or {}).get("nameWithOwner", "")
            if not repo:
                continue
            handled += 1
            try:
                pr = github.pr_view(repo, item["number"])
                case = classify(repo, {**pr, "number": item["number"]}, ctx.cfg.login)
                outcome = act(ctx, case)
            except Exception as e:  # noqa: BLE001 — item isolation
                outcome = f"error: {type(e).__name__}: {str(e)[:160]}"
                ctx.ledger.append(Ev.CASE_REVIEWED, f"{repo}#{item['number']}",
                                  reason="error", detail=outcome[:200])
            print(f"[steward] {repo}#{item['number']}: {outcome}")
        print(f"[steward] done: {handled} PRs reviewed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
