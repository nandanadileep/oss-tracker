"""Heartbeat: the watchdog that watches the watchers (DOMAIN_MODEL.md §10).

Tiny, dependency-free, and deliberately outside the main concurrency group:
if no successful run has committed state in >25h (silent cron death, the
60-day disable, total breakage), open ONE needs-human issue. Idempotent.
"""

from __future__ import annotations

import argparse
import sys

from .. import config as config_mod
from .. import gh as github
from ..events import Ev, Ledger
from ..ids import age, run_id

ALARM_TITLE = "[needs-human] harness_degraded: no successful run in >{h}h"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = config_mod.load()
    ledger = Ledger(cfg.ledger_path, run_id=run_id())
    alarm_h = cfg.limits.heartbeat_alarm_hours

    last_ok = None
    for ev in ledger.events():
        if ev.event == Ev.RUN_FINISHED and ev.data.get("outcome") == "ok" \
                and ev.subject != "heartbeat":
            last_ok = ev

    hours = age(last_ok.at).total_seconds() / 3600 if last_ok else float("inf")
    if hours <= alarm_h:
        ledger.append(Ev.HEARTBEAT_OK, "heartbeat", last_ok_hours=round(hours, 1))
        print(f"[heartbeat] ok — last successful run {hours:.1f}h ago")
        return 0

    title = ALARM_TITLE.format(h=alarm_h)
    ledger.append(Ev.HEARTBEAT_ALARM, "heartbeat",
                  last_ok_hours=None if hours == float("inf") else round(hours, 1))
    if args.dry_run:
        print(f"[heartbeat] ALARM (dry-run): would open issue: {title}")
        return 0

    # idempotent: one open alarm issue at a time
    try:
        existing = github.gh_json([
            "issue", "list", "-R", cfg.tracker_repo, "--state", "open",
            "--label", "needs-human", "--json", "title,number",
        ]) or []
        if any(i["title"].startswith("[needs-human] harness_degraded") for i in existing):
            print("[heartbeat] ALARM — issue already open")
            return 0
        github.gh(["issue", "create", "-R", cfg.tracker_repo,
                   "--title", title, "--label", "needs-human",
                   "--body",
                   "The harness has not completed a successful run in over "
                   f"{alarm_h}h. Likely causes: silently dropped cron, model chain "
                   "exhausted, credential expiry, or a crash loop.\n\n"
                   "Check the latest Actions runs, then close this issue once a "
                   "run goes green. The heartbeat will not duplicate this issue "
                   "while it stays open."], timeout=60)
        print(f"[heartbeat] ALARM — opened issue: {title}")
    except github.GhError as e:
        # the watchdog must never crash the workflow — a red heartbeat is itself noise
        print(f"[heartbeat] ALARM but could not open issue: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
