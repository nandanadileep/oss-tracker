"""Domain entities and their state machines (DOMAIN_MODEL.md §3-§6, §9).

Pure: no I/O, no GitHub, no clock beyond what callers pass in. Transitions are
explicit tables; illegal moves raise InvalidTransition rather than corrupting
state silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import Enum

from .ids import age, parse_iso, utcnow


class InvalidTransition(Exception):
    def __init__(self, entity: str, frm: str, to: str):
        super().__init__(f"{entity}: illegal transition {frm} -> {to}")


def _check(entity: str, table: dict[str, set[str]], frm: str, to: str) -> None:
    if to not in table.get(frm, set()):
        raise InvalidTransition(entity, frm, to)


# ── Candidate ──────────────────────────────────────────────────────────────

class CandidateState(str, Enum):
    DISCOVERED = "discovered"
    SCREENED = "screened"
    QUEUED = "queued"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ABANDONED = "abandoned"
    ESCALATED = "escalated"


_CANDIDATE = {
    CandidateState.DISCOVERED: {CandidateState.SCREENED, CandidateState.REJECTED},
    CandidateState.SCREENED: {CandidateState.QUEUED, CandidateState.REJECTED},
    CandidateState.QUEUED: {CandidateState.CLAIMED, CandidateState.EXPIRED, CandidateState.REJECTED},
    CandidateState.CLAIMED: {CandidateState.IN_PROGRESS, CandidateState.REJECTED},
    CandidateState.IN_PROGRESS: {CandidateState.DONE, CandidateState.ABANDONED, CandidateState.ESCALATED},
}

TIER_ORDER = {"A": 0, "B": 1, "C": 2, "F": 3}


@dataclass
class Candidate:
    repo: str
    issue_number: int
    state: CandidateState = CandidateState.DISCOVERED
    tier: str = "C"
    score: float = 0.0
    source: str = "unknown"
    discovered_at: str = ""
    reason: str = ""
    issue_title: str = ""

    @property
    def subject(self) -> str:
        return f"{self.repo}#{self.issue_number}"

    def advance(self, to: CandidateState, reason: str = "") -> "Candidate":
        _check("Candidate", _CANDIDATE, self.state, to)
        return replace(self, state=to, reason=reason or self.reason)

    def expired_by(self, ttl_days: int, now=None) -> bool:
        if not self.discovered_at:
            return False
        now = now or utcnow()
        return (now - parse_iso(self.discovered_at)) > timedelta(days=ttl_days)


# ── Contribution ───────────────────────────────────────────────────────────

class ContributionState(str, Enum):
    INTENT = "intent"
    FORKED = "forked"
    BRANCHED = "branched"
    PATCHED = "patched"
    VERIFIED = "verified"
    PR_OPENED = "pr_opened"
    AWAITING_CI = "awaiting_ci"
    CI_FAILED = "ci_failed"
    FIXING = "fixing"
    AWAITING_REVIEW = "awaiting_review"
    CHANGES_REQUESTED = "changes_requested"
    REVISING = "revising"
    MERGED = "merged"
    CLOSED_UNMERGED = "closed_unmerged"
    SUPERSEDED = "superseded"
    ABANDONED = "abandoned"
    ESCALATED = "escalated"


TERMINAL = {ContributionState.MERGED, ContributionState.CLOSED_UNMERGED,
            ContributionState.SUPERSEDED, ContributionState.ABANDONED}

_CONTRIBUTION = {
    ContributionState.INTENT: {ContributionState.FORKED},
    ContributionState.FORKED: {ContributionState.BRANCHED},
    ContributionState.BRANCHED: {ContributionState.PATCHED},
    ContributionState.PATCHED: {ContributionState.VERIFIED},
    ContributionState.VERIFIED: {ContributionState.PR_OPENED},
    ContributionState.PR_OPENED: {ContributionState.AWAITING_CI},
    ContributionState.AWAITING_CI: {ContributionState.AWAITING_REVIEW, ContributionState.CI_FAILED},
    ContributionState.CI_FAILED: {ContributionState.FIXING},
    ContributionState.FIXING: {ContributionState.AWAITING_CI},
    ContributionState.AWAITING_REVIEW: {
        ContributionState.CHANGES_REQUESTED, ContributionState.MERGED,
        ContributionState.CLOSED_UNMERGED, ContributionState.SUPERSEDED,
    },
    ContributionState.CHANGES_REQUESTED: {ContributionState.REVISING},
    ContributionState.REVISING: {ContributionState.AWAITING_REVIEW},
}


@dataclass
class Contribution:
    repo: str
    issue_number: int
    state: ContributionState = ContributionState.INTENT
    fork: str = ""
    branch: str = ""
    base_branch: str = ""
    base_sha: str = ""
    pr_number: int = 0
    fix_iterations: int = 0
    reason: str = ""

    @property
    def subject(self) -> str:
        return f"{self.repo}#{self.issue_number}"

    def advance(self, to: ContributionState, **changes) -> "Contribution":
        # abandoned / escalated are reachable from any non-terminal state
        if to in (ContributionState.ABANDONED, ContributionState.ESCALATED):
            if self.state in TERMINAL:
                raise InvalidTransition("Contribution", self.state, to)
            return replace(self, state=to, **changes)
        _check("Contribution", _CONTRIBUTION, self.state, to)
        if to is ContributionState.FIXING:
            changes.setdefault("fix_iterations", self.fix_iterations + 1)
        return replace(self, state=to, **changes)

    def may_fix_again(self, cap: int) -> bool:
        return self.fix_iterations < cap


# ── RepoRelationship ───────────────────────────────────────────────────────

class Standing(str, Enum):
    VIRGIN = "virgin"
    ENGAGED = "engaged"
    WELCOMED = "welcomed"
    COOLED_DOWN = "cooled_down"
    BLOCKED = "blocked"


class Signal(str, Enum):
    PR_OPENED = "pr_opened"
    PR_MERGED = "pr_merged"
    PR_CLOSED_UNMERGED = "pr_closed_unmerged"
    POSITIVE_INTERACTION = "positive_interaction"
    STOP_SIGNAL = "stop_signal"
    MANUAL_UNBLOCK = "manual_unblock"


@dataclass
class RepoRelationship:
    """Our standing with one repo — the reputation ledger (DOMAIN_MODEL.md §3).

    blocked is sticky: only Signal.MANUAL_UNBLOCK (Hari) leaves it.
    cooled_down expires by clock via effective_standing().
    """

    repo: str
    standing: Standing = Standing.VIRGIN
    prs_opened: int = 0
    prs_merged: int = 0
    prs_closed_unmerged: int = 0
    open_pr_count: int = 0
    cooldown_until: str = ""
    blocked_reason: str = ""
    last_engaged_at: str = ""
    notes: list[str] = field(default_factory=list)

    def apply(self, signal: Signal, at: str, cooldown_days: int = 30, detail: str = "") -> "RepoRelationship":
        r = replace(self, last_engaged_at=at, notes=list(self.notes))
        if signal is Signal.MANUAL_UNBLOCK:
            r.standing, r.blocked_reason, r.prs_closed_unmerged = Standing.ENGAGED, "", 0
            return r
        if r.standing is Standing.BLOCKED:
            return r  # sticky; record nothing but the timestamp
        if signal is Signal.STOP_SIGNAL:
            r.standing, r.blocked_reason = Standing.BLOCKED, detail or "maintainer stop signal"
        elif signal is Signal.PR_OPENED:
            r.prs_opened += 1
            r.open_pr_count += 1
            if r.standing is Standing.VIRGIN:
                r.standing = Standing.ENGAGED
        elif signal is Signal.PR_MERGED:
            r.prs_merged += 1
            r.open_pr_count = max(0, r.open_pr_count - 1)
            r.standing = Standing.WELCOMED
        elif signal is Signal.PR_CLOSED_UNMERGED:
            r.prs_closed_unmerged += 1
            r.open_pr_count = max(0, r.open_pr_count - 1)
            if r.prs_closed_unmerged >= 2:
                r.standing, r.blocked_reason = Standing.BLOCKED, "2 PRs closed unmerged"
            else:
                until = parse_iso(at) + timedelta(days=cooldown_days)
                r.standing = Standing.COOLED_DOWN
                r.cooldown_until = until.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        elif signal is Signal.POSITIVE_INTERACTION:
            if r.standing in (Standing.VIRGIN, Standing.ENGAGED):
                r.standing = Standing.WELCOMED
        if detail:
            r.notes.append(detail)
        return r

    def effective_standing(self, now=None) -> Standing:
        if self.standing is Standing.COOLED_DOWN and self.cooldown_until:
            if (now or utcnow()) >= parse_iso(self.cooldown_until):
                return Standing.ENGAGED
        return self.standing

    def may_open_new_pr(self, max_open_per_repo: int, now=None) -> tuple[bool, str]:
        s = self.effective_standing(now)
        if s is Standing.BLOCKED:
            return False, f"RELATIONSHIP_BLOCKED:{self.blocked_reason}"
        if s is Standing.COOLED_DOWN:
            return False, "COOLDOWN_ACTIVE"
        if self.open_pr_count >= max_open_per_repo:
            return False, "MAX_OPEN_PER_REPO"
        return True, ""


def fold_relationships(ledger_events, cooldown_days: int = 30) -> dict[str, RepoRelationship]:
    """Rebuild every RepoRelationship from the ledger (DOMAIN_MODEL.md §10)."""
    from .events import Ev

    signal_map = {
        Ev.PR_OPENED: Signal.PR_OPENED,
        Ev.CONTRIBUTION_MERGED: Signal.PR_MERGED,
        Ev.CONTRIBUTION_CLOSED: Signal.PR_CLOSED_UNMERGED,
    }
    rels: dict[str, RepoRelationship] = {}
    for ev in ledger_events:
        repo = ev.subject.split("#")[0]
        sig = signal_map.get(ev.event)
        if ev.event == Ev.MAINTAINER_INTERACTION:
            kind = ev.data.get("sentiment", "")
            if kind == "stop_signal":
                sig = Signal.STOP_SIGNAL
            elif kind == "positive":
                sig = Signal.POSITIVE_INTERACTION
        elif ev.event == Ev.RELATIONSHIP_CHANGED and ev.data.get("signal") == "manual_unblock":
            sig = Signal.MANUAL_UNBLOCK
        if sig is None:
            continue
        rel = rels.get(repo) or RepoRelationship(repo=repo)
        rels[repo] = rel.apply(sig, at=ev.at, cooldown_days=cooldown_days,
                               detail=ev.data.get("detail", ""))
    return rels


# ── StewardCase ────────────────────────────────────────────────────────────

class CaseReason(str, Enum):
    MAINTAINER_REPLIED = "maintainer_replied"
    CI_FAILED = "ci_failed"
    MERGE_CONFLICT = "merge_conflict"
    REPO_ARCHIVED = "repo_archived"
    ISSUE_FIXED_ELSEWHERE = "issue_fixed_elsewhere"
    STALE_NO_RESPONSE = "stale_no_response"
    CLA_BLOCKING = "cla_blocking"
    FORMAT_BOT_BLOCKING = "format_bot_blocking"
    HEALTHY = "healthy"


# priority: lower = handled first within a batch
CASE_PRIORITY = {
    CaseReason.MAINTAINER_REPLIED: 0,
    CaseReason.CLA_BLOCKING: 1,
    CaseReason.REPO_ARCHIVED: 2,
    CaseReason.ISSUE_FIXED_ELSEWHERE: 2,
    CaseReason.MERGE_CONFLICT: 3,
    CaseReason.CI_FAILED: 3,
    CaseReason.FORMAT_BOT_BLOCKING: 4,
    CaseReason.STALE_NO_RESPONSE: 5,
    CaseReason.HEALTHY: 9,
}


@dataclass
class StewardCase:
    repo: str
    pr_number: int
    reason: CaseReason = CaseReason.HEALTHY
    detail: str = ""

    @property
    def subject(self) -> str:
        return f"{self.repo}#{self.pr_number}"

    @property
    def priority(self) -> int:
        return CASE_PRIORITY[self.reason]


class NudgePolicy:
    """Anti-spam core (DOMAIN_MODEL.md §6). Pure decision over ledger history."""

    def __init__(self, interval_days: int, lifetime_cap: int):
        self.interval_days = interval_days
        self.lifetime_cap = lifetime_cap

    def may_nudge(self, subject: str, nudge_events: list, now=None) -> tuple[bool, str]:
        mine = [ev for ev in nudge_events if ev.subject == subject]
        if len(mine) >= self.lifetime_cap:
            return False, "nudge lifetime cap reached -> dormant"
        if mine:
            last = max(parse_iso(ev.at) for ev in mine)
            if ((now or utcnow()) - last).days < self.interval_days:
                return False, "nudge interval not elapsed"
        return True, ""


# ── Escalation ─────────────────────────────────────────────────────────────

class EscalationReason(str, Enum):
    CLA_SIGNATURE = "cla_signature"
    CREDENTIAL_ROTATION = "credential_rotation"
    CONFLICTING_REVIEW = "conflicting_review"
    SOCIAL = "social"
    NEW_DEPENDENCY = "new_dependency"
    TOO_LARGE = "too_large"
    CONFLICT_JUDGMENT = "conflict_judgment"
    POLICY_QUESTION = "policy_question"
    HARNESS_DEGRADED = "harness_degraded"
    ACCOUNT_RISK = "account_risk"


# what an open escalation blocks (DOMAIN_MODEL.md §9)
class BlockScope(str, Enum):
    ORG = "org"
    REPO = "repo"
    CONTRIBUTION = "contribution"
    GLOBAL = "global"


ESCALATION_SCOPE = {
    EscalationReason.CLA_SIGNATURE: BlockScope.ORG,
    EscalationReason.CREDENTIAL_ROTATION: BlockScope.GLOBAL,
    EscalationReason.CONFLICTING_REVIEW: BlockScope.CONTRIBUTION,
    EscalationReason.SOCIAL: BlockScope.CONTRIBUTION,
    EscalationReason.NEW_DEPENDENCY: BlockScope.CONTRIBUTION,
    EscalationReason.TOO_LARGE: BlockScope.CONTRIBUTION,
    EscalationReason.CONFLICT_JUDGMENT: BlockScope.CONTRIBUTION,
    EscalationReason.POLICY_QUESTION: BlockScope.REPO,
    EscalationReason.HARNESS_DEGRADED: BlockScope.GLOBAL,
    EscalationReason.ACCOUNT_RISK: BlockScope.GLOBAL,
}


@dataclass(frozen=True)
class Escalation:
    reason: EscalationReason
    subject: str  # org, repo, or repo#n depending on scope
    recommendation: str
    options: tuple[str, ...] = ("proceed", "skip", "block-repo")

    @property
    def scope(self) -> BlockScope:
        return ESCALATION_SCOPE[self.reason]

    def title(self) -> str:
        return f"[needs-human] {self.reason.value}: {self.subject}"

    def body(self) -> str:
        opts = "\n".join(f"- [ ] `RESOLVE: {o}`" for o in self.options)
        return (
            f"**Reason:** `{self.reason.value}`  \n"
            f"**Subject:** `{self.subject}`  \n"
            f"**Blocking scope:** `{self.scope.value}`\n\n"
            f"**Agent recommendation:** {self.recommendation}\n\n"
            f"Reply with one of (comment or tick):\n{opts}\n\n"
            f"_Opened automatically by the oss harness. The next run re-reads this issue;_\n"
            f"_its blocking scope is skipped until resolved._"
        )


def stop_signal_in(text: str) -> bool:
    """Detect maintainer stop-signals (DOMAIN_MODEL.md §3). Conservative on purpose."""
    t = text.lower()
    needles = (
        "please don't", "please do not", "stop submitting", "stop opening",
        "we don't accept ai", "we do not accept ai", "no ai-generated",
        "no ai generated", "unwelcome", "do not contribute again", "spam",
    )
    return any(n in t for n in needles)
