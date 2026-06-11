import pytest

from harness.domain import (Candidate, CandidateState, Contribution,
                            ContributionState, Escalation, EscalationReason,
                            InvalidTransition, NudgePolicy, RepoRelationship,
                            Signal, Standing, fold_relationships,
                            stop_signal_in)
from harness.events import Ev
from harness.ids import iso_now


# ── candidate ──────────────────────────────────────────────────────────────

def test_candidate_happy_path():
    c = Candidate("o/r", 1)
    for st in (CandidateState.SCREENED, CandidateState.QUEUED,
               CandidateState.CLAIMED, CandidateState.IN_PROGRESS, CandidateState.DONE):
        c = c.advance(st)
    assert c.state is CandidateState.DONE


def test_candidate_illegal_jump_raises():
    with pytest.raises(InvalidTransition):
        Candidate("o/r", 1).advance(CandidateState.DONE)


def test_candidate_ttl_expiry():
    c = Candidate("o/r", 1, discovered_at="2020-01-01T00:00:00.000000Z")
    assert c.expired_by(ttl_days=21)


# ── contribution ───────────────────────────────────────────────────────────

def test_contribution_full_lifecycle_to_merged():
    c = Contribution("o/r", 7)
    path = [ContributionState.FORKED, ContributionState.BRANCHED,
            ContributionState.PATCHED, ContributionState.VERIFIED,
            ContributionState.PR_OPENED, ContributionState.AWAITING_CI,
            ContributionState.CI_FAILED, ContributionState.FIXING,
            ContributionState.AWAITING_CI, ContributionState.AWAITING_REVIEW,
            ContributionState.CHANGES_REQUESTED, ContributionState.REVISING,
            ContributionState.AWAITING_REVIEW, ContributionState.MERGED]
    for st in path:
        c = c.advance(st)
    assert c.state is ContributionState.MERGED
    assert c.fix_iterations == 1


def test_contribution_abandon_from_anywhere_but_terminal():
    c = Contribution("o/r", 7).advance(ContributionState.FORKED)
    assert c.advance(ContributionState.ABANDONED).state is ContributionState.ABANDONED
    merged = Contribution("o/r", 7, state=ContributionState.MERGED)
    with pytest.raises(InvalidTransition):
        merged.advance(ContributionState.ABANDONED)


def test_fix_iteration_cap():
    c = Contribution("o/r", 1, fix_iterations=3)
    assert not c.may_fix_again(cap=3)


# ── relationship standing ──────────────────────────────────────────────────

def test_merge_promotes_to_welcomed():
    r = RepoRelationship("o/r").apply(Signal.PR_OPENED, iso_now())
    r = r.apply(Signal.PR_MERGED, iso_now())
    assert r.standing is Standing.WELCOMED
    assert r.open_pr_count == 0


def test_first_close_cools_second_blocks():
    now = iso_now()
    r = RepoRelationship("o/r").apply(Signal.PR_OPENED, now)
    r = r.apply(Signal.PR_CLOSED_UNMERGED, now)
    assert r.standing is Standing.COOLED_DOWN
    assert r.effective_standing() is Standing.COOLED_DOWN
    r = r.apply(Signal.PR_CLOSED_UNMERGED, now)
    assert r.standing is Standing.BLOCKED


def test_cooldown_expires_by_clock():
    r = RepoRelationship("o/r").apply(Signal.PR_CLOSED_UNMERGED,
                                      "2020-01-01T00:00:00.000000Z", cooldown_days=30)
    assert r.effective_standing() is Standing.ENGAGED  # long past 2020


def test_stop_signal_blocks_forever_until_manual_unblock():
    r = RepoRelationship("o/r").apply(Signal.STOP_SIGNAL, iso_now(), detail="please stop")
    assert r.standing is Standing.BLOCKED
    r = r.apply(Signal.PR_MERGED, iso_now())  # sticky
    assert r.standing is Standing.BLOCKED
    r = r.apply(Signal.MANUAL_UNBLOCK, iso_now())
    assert r.standing is Standing.ENGAGED


def test_may_open_new_pr_gates():
    r = RepoRelationship("o/r", open_pr_count=2)
    ok, why = r.may_open_new_pr(max_open_per_repo=2)
    assert not ok and why == "MAX_OPEN_PER_REPO"


def test_fold_relationships_from_ledger(ledger):
    ledger.append(Ev.PR_OPENED, "o/r#1")
    ledger.append(Ev.CONTRIBUTION_MERGED, "o/r#1")
    ledger.append(Ev.MAINTAINER_INTERACTION, "x/y#9", sentiment="stop_signal",
                  detail="we don't accept AI PRs")
    rels = fold_relationships(ledger.events())
    assert rels["o/r"].standing is Standing.WELCOMED
    assert rels["x/y"].standing is Standing.BLOCKED


# ── nudge policy ───────────────────────────────────────────────────────────

class _Ev:
    def __init__(self, subject, at):
        self.subject, self.at = subject, at


def test_nudge_interval_and_lifetime():
    p = NudgePolicy(interval_days=30, lifetime_cap=2)
    assert p.may_nudge("o/r#1", [])[0]
    recent = [_Ev("o/r#1", iso_now())]
    assert not p.may_nudge("o/r#1", recent)[0]
    old = [_Ev("o/r#1", "2020-01-01T00:00:00.000000Z"),
           _Ev("o/r#1", "2020-02-01T00:00:00.000000Z")]
    ok, why = p.may_nudge("o/r#1", old)
    assert not ok and "lifetime" in why


# ── escalation & stop signals ──────────────────────────────────────────────

def test_escalation_scope_and_body():
    e = Escalation(EscalationReason.CLA_SIGNATURE, "someorg", "Sign the CLA")
    assert e.scope.value == "org"
    assert "RESOLVE:" in e.body() and "Blocking scope" in e.body()
    assert e.title().startswith("[needs-human]")


def test_stop_signal_detection():
    assert stop_signal_in("Please don't open more of these.")
    assert stop_signal_in("We do not accept AI generated patches")
    assert not stop_signal_in("Thanks! Could you rebase?")
