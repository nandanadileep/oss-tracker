from harness.domain import Escalation, EscalationReason
from harness.events import Ev
from harness.executor import Executor
from harness.policy import ActionValidator, load_secret_patterns

PATTERNS = load_secret_patterns()


def _executor(cfg, ledger, dry_run=True):
    v = ActionValidator(cfg, ledger, {}, patterns=PATTERNS)
    return Executor(cfg, ledger, v, dry_run=dry_run)


def test_dry_run_executes_nothing_but_journals(cfg, ledger):
    ex = _executor(cfg, ledger)
    a = ex.propose("post_comment", "o/r", 5, body="A fine and unique comment.")
    r = ex.execute(a)
    assert r.outcome == "dry_run"
    events = [e.event for e in ledger.events()]
    assert Ev.ACTION_PROPOSED in events and Ev.ACTION_EXECUTED in events


def test_rejected_action_journals_codes(cfg, ledger):
    ex = _executor(cfg, ledger)
    a = ex.propose("close_pr", "o/r", 5)  # missing reason
    r = ex.execute(a)
    assert r.outcome == "rejected" and "CLOSE_NEEDS_REASON" in r.detail
    rejected = [e for e in ledger.events() if e.event == Ev.ACTION_REJECTED]
    assert rejected and "CLOSE_NEEDS_REASON" in rejected[0].data["codes"]


def test_replay_same_action_id_is_idempotent(cfg, ledger):
    ex = _executor(cfg, ledger)
    a = ex.propose("post_comment", "o/r", 5, body="Words that are quite unique here.")
    assert ex.execute(a).outcome == "dry_run"
    assert ex.execute(a).outcome == "rejected"  # DUPLICATE_ACTION_ID


def test_escalation_dedupes_by_reason_and_subject(cfg, ledger):
    ex = _executor(cfg, ledger)
    esc = Escalation(EscalationReason.CLA_SIGNATURE, "bigorg", "Sign it")
    r1 = ex.escalate(esc)
    r2 = ex.escalate(esc)
    assert r1.outcome == "dry_run" and r2.detail == "escalation already open"
    assert len([e for e in ledger.events() if e.event == Ev.ESCALATION_OPENED]) == 1


def test_open_escalation_keys_tracks_resolution(cfg, ledger):
    ex = _executor(cfg, ledger)
    ex.escalate(Escalation(EscalationReason.CLA_SIGNATURE, "bigorg", "Sign it"))
    assert "cla_signature:bigorg" in ex.open_escalation_keys()
    ledger.append(Ev.ESCALATION_RESOLVED, "bigorg", key="cla_signature:bigorg")
    assert "cla_signature:bigorg" not in ex.open_escalation_keys()
