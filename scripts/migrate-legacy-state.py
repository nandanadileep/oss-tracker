#!/usr/bin/env python3
"""One-time migration: seed ledger.jsonl from the legacy queue.json.

Legacy queue items become candidate_discovered + candidate_screened events
(tier B, source legacy_queue); legacy done items are recorded as consumed so
they never re-enter the queue. Idempotent: skips subjects already in the ledger.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import config as config_mod
from harness.events import Ev, Ledger


def main() -> int:
    cfg = config_mod.load()
    legacy_path = cfg.state_dir / "queue.json"
    if not legacy_path.exists():
        print("no legacy queue.json; nothing to migrate")
        return 0
    legacy = json.loads(legacy_path.read_text())
    if legacy.get("_version") == 2:
        print("queue.json already migrated (v2 view)")
        return 0

    ledger = Ledger(cfg.ledger_path, run_id="migration")
    seen = ledger.subjects_with(Ev.CANDIDATE_DISCOVERED)
    queued = done = 0
    for subject in legacy.get("queue", []):
        if not isinstance(subject, str) or "#" not in subject or subject in seen:
            continue
        ledger.append(Ev.CANDIDATE_DISCOVERED, subject, source="legacy_queue")
        ledger.append(Ev.CANDIDATE_SCREENED, subject, score=6.0, tier="B")
        queued += 1
    for item in legacy.get("done", []):
        subject = item.get("key", "") if isinstance(item, dict) else item
        if not isinstance(subject, str) or "#" not in subject or subject in seen:
            continue
        seen.add(subject)
        ledger.append(Ev.CANDIDATE_DISCOVERED, subject, source="legacy_queue")
        ledger.append(Ev.CANDIDATE_REJECTED, subject, reason="legacy_done")
        done += 1
    print(f"migrated: {queued} queued, {done} done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
