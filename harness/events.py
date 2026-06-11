"""The ledger: append-only domain events, and the folds that turn them into views.

The ledger is the source of truth (DOMAIN_MODEL.md §10-11). Every other state
file is a rebuildable fold over it. Appends are flushed per event so a run
killed mid-batch keeps everything up to the in-flight item.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .ids import iso_now, new_id, parse_iso


class Ev:
    """Event vocabulary (DOMAIN_MODEL.md §11). Subjects are 'owner/repo' or 'owner/repo#n'."""

    # discovery
    CANDIDATE_DISCOVERED = "candidate_discovered"
    CANDIDATE_SCREENED = "candidate_screened"
    CANDIDATE_REJECTED = "candidate_rejected"
    CANDIDATE_EXPIRED = "candidate_expired"
    CANDIDATE_CLAIMED = "candidate_claimed"
    # policy
    PREFLIGHT_PASSED = "preflight_passed"
    PREFLIGHT_BLOCKED = "preflight_blocked"
    RELATIONSHIP_CHANGED = "relationship_changed"
    # contribution
    CONTRIBUTION_STARTED = "contribution_started"
    FORK_READY = "fork_ready"
    BRANCH_CREATED = "branch_created"
    PATCH_PROPOSED = "patch_proposed"
    PATCH_REJECTED = "patch_rejected"
    PATCH_APPLIED = "patch_applied"
    VERIFICATION_RAN = "verification_ran"
    PR_OPENED = "pr_opened"
    PR_UPDATED = "pr_updated"
    CI_OBSERVED = "ci_observed"
    REVIEW_RECEIVED = "review_received"
    CONTRIBUTION_MERGED = "contribution_merged"
    CONTRIBUTION_CLOSED = "contribution_closed"
    CONTRIBUTION_ABANDONED = "contribution_abandoned"
    CONTRIBUTION_ESCALATED = "contribution_escalated"
    # stewardship
    CASE_REVIEWED = "case_reviewed"
    NUDGE_POSTED = "nudge_posted"
    REPLY_POSTED = "reply_posted"
    PR_REBASED = "pr_rebased"
    PR_WITHDRAWN = "pr_withdrawn"
    CASE_DORMANT = "case_dormant"
    MAINTAINER_INTERACTION = "maintainer_interaction"
    # runtime
    MODEL_CALL = "model_call"
    MODEL_CHAIN_ADVANCED = "model_chain_advanced"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INJECTION_SUSPECTED = "injection_suspected"
    # execution
    ACTION_PROPOSED = "action_proposed"
    ACTION_REJECTED = "action_rejected"
    ACTION_EXECUTED = "action_executed"
    # escalation
    ESCALATION_OPENED = "escalation_opened"
    ESCALATION_RESOLVED = "escalation_resolved"
    # operations
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    STATE_COMMITTED = "state_committed"
    HEARTBEAT_OK = "heartbeat_ok"
    HEARTBEAT_ALARM = "heartbeat_alarm"
    GLOBAL_PAUSE_SET = "global_pause_set"
    GLOBAL_PAUSE_CLEARED = "global_pause_cleared"


@dataclass(frozen=True)
class Event:
    id: str
    at: str
    run_id: str
    event: str
    subject: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"id": self.id, "at": self.at, "run_id": self.run_id,
             "event": self.event, "subject": self.subject, "data": self.data},
            separators=(",", ":"), ensure_ascii=False,
        )


class Ledger:
    """Append-only JSONL journal. Reads are tolerant: corrupt lines are skipped
    and counted, never fatal (DOMAIN_MODEL.md §10 quarantine rule)."""

    def __init__(self, path: Path | str, run_id: str = "local"):
        self.path = Path(path)
        self.run_id = run_id
        self.skipped_lines = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, subject: str, **data: Any) -> Event:
        ev = Event(id=new_id("ev"), at=iso_now(), run_id=self.run_id,
                   event=event, subject=subject, data=data)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(ev.to_json() + "\n")
            f.flush()
        return ev

    def events(self) -> Iterator[Event]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    yield Event(id=raw.get("id", "?"), at=raw["at"],
                                run_id=raw.get("run_id", "?"), event=raw["event"],
                                subject=raw.get("subject", ""), data=raw.get("data", {}))
                except (json.JSONDecodeError, KeyError):
                    self.skipped_lines += 1

    # ── folds ────────────────────────────────────────────────────────────

    def last(self, event: str | None = None, subject: str | None = None) -> Event | None:
        found = None
        for ev in self.events():
            if event and ev.event != event:
                continue
            if subject and ev.subject != subject:
                continue
            found = ev
        return found

    def day_counts(self, day: str) -> Counter:
        """Counts of every event type on a given UTC day ('YYYY-MM-DD')."""
        c: Counter = Counter()
        for ev in self.events():
            if ev.at.startswith(day):
                c[ev.event] += 1
        return c

    def in_window(self, event: str, hours: float, now=None) -> list[Event]:
        from .ids import utcnow
        now = now or utcnow()
        out = []
        for ev in self.events():
            if ev.event == event and (now - parse_iso(ev.at)).total_seconds() <= hours * 3600:
                out.append(ev)
        return out

    def subjects_with(self, event: str) -> set[str]:
        return {ev.subject for ev in self.events() if ev.event == event}

    def global_pause_active(self) -> bool:
        state = False
        for ev in self.events():
            if ev.event == Ev.GLOBAL_PAUSE_SET:
                state = True
            elif ev.event == Ev.GLOBAL_PAUSE_CLEARED:
                state = False
        return state

    def recent_texts(self, event_names: set[str], limit: int = 50) -> list[str]:
        """Bodies of our recent outbound comments — uniformity-lint input."""
        texts = [ev.data.get("body", "") for ev in self.events() if ev.event in event_names]
        return [t for t in texts if t][-limit:]
