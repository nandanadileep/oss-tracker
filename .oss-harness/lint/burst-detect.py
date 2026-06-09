#!/usr/bin/env python3
"""
Burst detector.

Flags when too many of Hari's PRs land in too short a window.
This is the "red flag" behavior that makes a maintainer's profile scan
go from "active contributor" to "automated agent."

Usage:
    burst-detect.py [--window HOURS] [--max N] [--data PATH]

Exit codes:
    0  no burst
    1  burst detected
    2  warn (close to threshold)
"""
import argparse, json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_DATA = Path(__file__).parent.parent / "data" / "prs_open.json"


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--window", type=int, default=24, help="Window in hours")
    p.add_argument("--max", type=int, default=3, help="Max PRs allowed in window")
    p.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if not args.data.exists():
        print(f"burst-detect: data not found at {args.data}", file=sys.stderr)
        return 0

    prs = json.load(open(args.data))
    # Sort by createdAt
    prs.sort(key=lambda r: parse(r["createdAt"]))

    # Group by sliding window
    bursts = []
    for i, pr in enumerate(prs):
        window_start = parse(pr["createdAt"]) - timedelta(hours=args.window)
        window = [r for r in prs if window_start <= parse(r["createdAt"]) <= parse(pr["createdAt"])]
        if len(window) > args.max:
            bursts.append({
                "anchor": f"{pr['repository']['nameWithOwner']}#{pr['number']}",
                "anchor_created": pr["createdAt"],
                "count": len(window),
                "window_hours": args.window,
                "members": [f"{r['repository']['nameWithOwner']}#{r['number']}" for r in window],
            })

    # Dedupe bursts by anchor
    seen = set()
    unique = []
    for b in bursts:
        if b["anchor"] in seen: continue
        seen.add(b["anchor"])
        unique.append(b)

    if args.json:
        print(json.dumps({"bursts": unique, "threshold": args.max, "window_hours": args.window}, indent=2))
    else:
        if unique:
            print(f"burst-detect: BURST DETECTED — {len(unique)} window(s) exceed {args.max} PRs in {args.window}h")
            for b in unique[:5]:
                print(f"  {b['anchor']} ({b['count']} PRs in {b['window_hours']}h)")
        else:
            print(f"burst-detect: OK — no window with more than {args.max} PRs in {args.window}h")

    return 1 if unique else 0


if __name__ == "__main__":
    sys.exit(main())
