"""Deterministic gates: preflight, content lint, secret scan, ActionValidator.

This module is the spam-prevention and injection-defense layer. Nothing here
calls a model; nothing here can be talked out of a rule (DOMAIN_MODEL.md §8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from .config import Config
from .domain import RepoRelationship, Standing
from .events import Ev, Ledger
from .ids import today

# ── secret scanning ────────────────────────────────────────────────────────

_DEFAULT_PATTERNS = [
    r"ghp_[A-Za-z0-9]{36,}", r"github_pat_[A-Za-z0-9_]{82,}",
    r"gh[ousr]_[A-Za-z0-9]{36,}", r"sk-ant-[A-Za-z0-9\-]{32,}",
    r"sk-proj-[A-Za-z0-9\-_]{20,}", r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED |)PRIVATE KEY-----",
    r"xox[baprs]-[A-Za-z0-9-]{10,}", r"sk_live_[A-Za-z0-9]{24,}",
    r"AIza[0-9A-Za-z\-_]{35}", r"npm_[A-Za-z0-9]{36}",
]


def load_secret_patterns(path: Path | str | None = None) -> list[re.Pattern]:
    lines: list[str] = []
    if path and Path(path).exists():
        for raw in Path(path).read_text().splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#"):
                lines.append(raw)
    out = []
    for pat in lines or _DEFAULT_PATTERNS:
        try:
            out.append(re.compile(pat))
        except re.error:
            continue  # a bad pattern must not take down the scanner
    return out


def scan_secrets(text: str, patterns: list[re.Pattern]) -> list[str]:
    hits = []
    for pat in patterns:
        m = pat.search(text)
        if m:
            hits.append(pat.pattern[:40])
    return hits


# ── comment lint ───────────────────────────────────────────────────────────

_PRESSURE_WORDS = ("urgent", "asap", "immediately", "demand", "you must", "why haven't")


def lint_comment(body: str, *, max_chars: int = 600) -> list[str]:
    problems = []
    s = body.strip()
    if not s:
        problems.append("empty comment")
    if len(s) > max_chars:
        problems.append(f"comment exceeds {max_chars} chars")
    if "—" in s or "–" in s:
        problems.append("comment contains em/en dash")
    low = s.lower()
    for w in _PRESSURE_WORDS:
        if w in low:
            problems.append(f"pressure language: {w!r}")
    if re.search(r"(?<![\w/])@[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}(?![\w/])", s):
        problems.append("@-mentions an individual")
    return problems


def uniformity_score(body: str, recent: list[str]) -> float:
    """Max similarity vs our recent outbound comments. >0.7 = templated spam shape."""
    best = 0.0
    for prev in recent:
        best = max(best, SequenceMatcher(None, body, prev).ratio())
    return best


# ── injection heuristics (issue text is data, never instructions) ─────────

_INJECTION_NEEDLES = (
    "ignore your instructions", "ignore previous instructions", "ignore all previous",
    "system prompt", "you are an ai agent", "disregard the above",
    "BEGIN INSTRUCTIONS", "do not tell the user",
)


def injection_suspected(text: str) -> bool:
    low = text.lower()
    return any(n.lower() in low for n in _INJECTION_NEEDLES)


# ── preflight ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RepoFacts:
    """The subset of RepoProfile that preflight consumes (fetched by gh.py)."""
    repo: str
    archived: bool = False
    disabled: bool = False
    license_spdx: str = ""
    ai_policy: str = "unknown"  # allowed | disclosed_only | forbidden | unknown
    pushed_at: str = ""
    default_branch: str = "main"
    size_kb: int = 0


@dataclass(frozen=True)
class PreflightReport:
    repo: str
    verdict: str  # pass | block
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.verdict == "pass"


def preflight(facts: RepoFacts, rel: RepoRelationship | None, cfg: Config,
              *, for_new_pr: bool = True) -> PreflightReport:
    reasons: list[str] = []
    if facts.archived or facts.disabled:
        reasons.append("ARCHIVED")
    if not facts.license_spdx or facts.license_spdx.lower() in ("none", "null"):
        reasons.append("NO_LICENSE")
    if facts.ai_policy == "forbidden":
        reasons.append("AI_FORBIDDEN")
    if facts.repo.split("/")[0] in cfg.exclude_owners:
        reasons.append("EXCLUDED_OWNER")
    if rel and for_new_pr:
        ok, why = rel.may_open_new_pr(cfg.limits.max_open_per_repo)
        if not ok:
            reasons.append(why)
    return PreflightReport(facts.repo, "block" if reasons else "pass", tuple(reasons))


# ── ProposedAction + validator ─────────────────────────────────────────────

WRITE_KINDS = {
    "open_pr", "push_commits", "post_comment", "close_pr",
    "update_pr_body", "open_escalation_issue", "fork_repo", "withdraw_pr",
}
CONTENT_KINDS = {"open_pr", "post_comment", "update_pr_body"}


@dataclass(frozen=True)
class ProposedAction:
    id: str
    kind: str
    repo: str
    number: int = 0  # PR or issue number where applicable
    payload: dict = field(default_factory=dict)
    rationale: str = ""

    @property
    def subject(self) -> str:
        return f"{self.repo}#{self.number}" if self.number else self.repo


@dataclass(frozen=True)
class Rejection:
    code: str
    detail: str = ""


class ActionValidator:
    """Every external write passes ALL gates or dies with reason codes (§8)."""

    # ledger events that count as "a comment we posted"
    _COMMENT_EVENTS = {Ev.NUDGE_POSTED, Ev.REPLY_POSTED}

    def __init__(self, cfg: Config, ledger: Ledger,
                 relationships: dict[str, RepoRelationship], patterns=None):
        self.cfg = cfg
        self.ledger = ledger
        self.rels = relationships
        self.patterns = patterns if patterns is not None else load_secret_patterns(cfg.secret_patterns_file)

    def validate(self, action: ProposedAction) -> list[Rejection]:
        rej: list[Rejection] = []
        if action.kind not in WRITE_KINDS:
            return [Rejection("UNKNOWN_KIND", action.kind)]

        if self.ledger.global_pause_active() and action.kind != "open_escalation_issue":
            return [Rejection("GLOBAL_PAUSE")]

        rej += self._rate_gates(action)
        rej += self._relationship_gates(action)
        rej += self._content_gates(action)
        rej += self._consistency_gates(action)
        rej += self._idempotency_gates(action)
        return rej

    # 1. rate
    def _rate_gates(self, a: ProposedAction) -> list[Rejection]:
        out = []
        counts = self.ledger.day_counts(today())
        lim = self.cfg.limits
        if a.kind == "open_pr":
            if counts[Ev.PR_OPENED] >= lim.daily_new_pr_cap:
                out.append(Rejection("DAILY_PR_CAP", f"{lim.daily_new_pr_cap}/day"))
            burst = self.ledger.in_window(Ev.PR_OPENED, lim.burst_window_hours)
            if len(burst) >= lim.burst_window_max_prs:
                out.append(Rejection("BURST_WINDOW",
                                     f">{lim.burst_window_max_prs} PRs in {lim.burst_window_hours}h"))
        if a.kind == "post_comment":
            posted = sum(counts[e] for e in self._COMMENT_EVENTS)
            if posted >= lim.daily_comment_cap:
                out.append(Rejection("DAILY_COMMENT_CAP", f"{lim.daily_comment_cap}/day"))
        return out

    # 2. relationship
    def _relationship_gates(self, a: ProposedAction) -> list[Rejection]:
        rel = self.rels.get(a.repo)
        if not rel:
            return []
        standing = rel.effective_standing()
        # tending/withdrawing our own existing PRs is always allowed
        if a.kind in ("close_pr", "withdraw_pr", "update_pr_body", "open_escalation_issue"):
            return []
        if standing is Standing.BLOCKED:
            return [Rejection("RELATIONSHIP_BLOCKED", rel.blocked_reason)]
        if a.kind in ("open_pr", "fork_repo"):
            ok, why = rel.may_open_new_pr(self.cfg.limits.max_open_per_repo)
            if not ok:
                return [Rejection(why.split(":")[0], why)]
        return []

    # 3. content
    def _content_gates(self, a: ProposedAction) -> list[Rejection]:
        if a.kind not in CONTENT_KINDS:
            return []
        out = []
        body = a.payload.get("body", "")
        hits = scan_secrets(body, self.patterns)
        if hits:
            out.append(Rejection("SECRET_IN_CONTENT", ";".join(hits)))
        if a.kind == "post_comment":
            for p in lint_comment(body, max_chars=self.cfg.limits.__dict__.get("max_comment_chars", 600)):
                out.append(Rejection("COMMENT_LINT", p))
            recent = self.ledger.recent_texts(self._COMMENT_EVENTS)
            if uniformity_score(body, recent) > 0.7:
                out.append(Rejection("COMMENT_UNIFORMITY", "too similar to recent comments"))
        if a.kind == "open_pr" and self.cfg.disclosure not in body:
            out.append(Rejection("MISSING_DISCLOSURE"))
        return out

    # 4. consistency
    def _consistency_gates(self, a: ProposedAction) -> list[Rejection]:
        out = []
        if a.kind in ("close_pr", "withdraw_pr") and not a.payload.get("reason"):
            out.append(Rejection("CLOSE_NEEDS_REASON"))
        if a.kind == "withdraw_pr" and not a.payload.get("evidence"):
            out.append(Rejection("WITHDRAW_NEEDS_EVIDENCE",
                                 "stop-signal or supersession evidence required"))
        if a.kind == "push_commits" and a.payload.get("force") and \
                not a.payload.get("remote", "").startswith(f"{self.cfg.login}/"):
            out.append(Rejection("FORCE_PUSH_OUTSIDE_FORK", a.payload.get("remote", "")))
        return out

    # 5. idempotency
    def _idempotency_gates(self, a: ProposedAction) -> list[Rejection]:
        for ev in self.ledger.events():
            if ev.event == Ev.ACTION_EXECUTED and ev.data.get("action_id") == a.id:
                return [Rejection("DUPLICATE_ACTION_ID", a.id)]
        if a.kind == "open_pr":
            for ev in self.ledger.events():
                if ev.event == Ev.PR_OPENED and ev.subject == a.subject:
                    return [Rejection("PR_ALREADY_OPENED", a.subject)]
        return []
