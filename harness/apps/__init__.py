"""Entrypoints. Each app: run_started → per-item try/except → run_finished.

A run that finds nothing to do exits 0 — red means harness malfunction only
(DOMAIN_MODEL.md §10).
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from dataclasses import dataclass

from .. import config as config_mod
from ..domain import fold_relationships
from ..events import Ev, Ledger
from ..executor import Executor
from ..ids import run_id, today
from ..policy import ActionValidator


@dataclass
class RunContext:
    cfg: config_mod.Config
    ledger: Ledger
    executor: Executor
    dry_run: bool


@contextlib.contextmanager
def harness_run(app: str, *, dry_run: bool):
    cfg = config_mod.load()
    ledger = Ledger(cfg.ledger_path, run_id=run_id())
    rels = fold_relationships(ledger.events(), cfg.limits.cooldown_after_close_days)
    validator = ActionValidator(cfg, ledger, rels)
    executor = Executor(cfg, ledger, validator, dry_run=dry_run)
    ledger.append(Ev.RUN_STARTED, app, dry_run=dry_run)

    if ledger.global_pause_active():
        print(f"[{app}] GLOBAL PAUSE active — no work performed", file=sys.stderr)
        ledger.append(Ev.RUN_FINISHED, app, outcome="paused")
        sys.exit(0)

    ctx = RunContext(cfg, ledger, executor, dry_run)
    try:
        yield ctx
    except Exception:
        ledger.append(Ev.RUN_FINISHED, app, outcome="crashed", dry_run=dry_run,
                      error=traceback.format_exc()[-1500:])
        raise
    ledger.append(Ev.RUN_FINISHED, app, outcome="ok", dry_run=dry_run)


def already_succeeded_today(ledger: Ledger, app: str) -> bool:
    """Same-day re-run guard: a delayed cron + manual dispatch double is a no-op.

    Only REAL runs count — a dry-run rehearsal must never block the live run
    (events without the dry_run flag predate it and are treated as dry).
    """
    for ev in ledger.events():
        if ev.event == Ev.RUN_FINISHED and ev.subject == app \
                and ev.data.get("outcome") == "ok" \
                and ev.data.get("dry_run") is False \
                and ev.at.startswith(today()):
            return True
    return False
