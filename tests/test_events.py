from harness.events import Ev, Ledger


def test_append_and_read_roundtrip(ledger):
    ledger.append(Ev.PR_OPENED, "o/r#1", url="http://x")
    ledger.append(Ev.PR_OPENED, "o/r#2")
    evs = list(ledger.events())
    assert [e.subject for e in evs] == ["o/r#1", "o/r#2"]
    assert evs[0].data["url"] == "http://x"


def test_corrupt_lines_are_skipped_not_fatal(ledger):
    ledger.append(Ev.PR_OPENED, "o/r#1")
    ledger.path.open("a").write("{broken json\nnot even json\n")
    ledger.append(Ev.PR_OPENED, "o/r#2")
    assert len(list(ledger.events())) == 2
    assert ledger.skipped_lines == 2


def test_day_counts_and_window(ledger):
    ledger.append(Ev.PR_OPENED, "o/r#1")
    ledger.append(Ev.NUDGE_POSTED, "o/r#2", body="hi")
    from harness.ids import today
    counts = ledger.day_counts(today())
    assert counts[Ev.PR_OPENED] == 1
    assert len(ledger.in_window(Ev.PR_OPENED, hours=1)) == 1
    assert len(ledger.in_window(Ev.PR_OPENED, hours=0)) == 0


def test_global_pause_set_and_cleared(ledger):
    assert not ledger.global_pause_active()
    ledger.append(Ev.GLOBAL_PAUSE_SET, "harness", reason="account_risk")
    assert ledger.global_pause_active()
    ledger.append(Ev.GLOBAL_PAUSE_CLEARED, "harness")
    assert not ledger.global_pause_active()


def test_recent_texts_for_uniformity(ledger):
    for i in range(3):
        ledger.append(Ev.NUDGE_POSTED, f"o/r#{i}", body=f"text {i}")
    assert ledger.recent_texts({Ev.NUDGE_POSTED}) == ["text 0", "text 1", "text 2"]


def test_missing_file_yields_nothing(tmp_path):
    assert list(Ledger(tmp_path / "absent.jsonl").events()) == []


def test_same_day_guard_ignores_dry_runs(ledger):
    from harness.apps import already_succeeded_today
    ledger.append(Ev.RUN_FINISHED, "contribute", outcome="ok", dry_run=True)
    assert not already_succeeded_today(ledger, "contribute")
    ledger.append(Ev.RUN_FINISHED, "contribute", outcome="ok")  # legacy, no flag
    assert not already_succeeded_today(ledger, "contribute")
    ledger.append(Ev.RUN_FINISHED, "contribute", outcome="ok", dry_run=False)
    assert already_succeeded_today(ledger, "contribute")
