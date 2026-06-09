#!/usr/bin/env python3
"""
Title uniformity check.

Flags when an unusual number of PR titles follow a single template.
A real contributor has varied titles; an automated agent often produces
a tight cluster of nearly-identical phrasing.

Usage:
    title-uniformity.py [--data PATH] [--min-unique-ratio R]

Exit codes:
    0  titles are varied enough
    1  too uniform (most titles collapse to a small set of templates)
"""
import argparse, json, re, sys
from collections import Counter
from pathlib import Path

DEFAULT_DATA = Path(__file__).parent.parent / "data" / "prs_open.json"

# Normalize a title to its "template" by replacing concrete tokens
def templatize(t):
    t = t.lower().strip()
    t = re.sub(r"\b[a-z][a-z0-9_]+\.[a-z_]+\b", "<func>", t)         # foo.bar
    t = re.sub(r"\b[a-z][a-z0-9_]*\(\)", "<call>", t)               # foo()
    t = re.sub(r"\bdef\s+\w+", "def <name>", t)                     # def foo
    t = re.sub(r"\b[a-z]+(?:[-_][a-z0-9]+)+\b", "<slug>", t)         # kebab/snake slugs
    t = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", t)                      # numbers
    t = re.sub(r"\s+", " ", t)
    return t


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, default=DEFAULT_DATA)
    p.add_argument("--min-unique-ratio", type=float, default=0.5,
                   help="Fraction of titles that must be unique (default 0.5)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if not args.data.exists():
        print(f"title-uniformity: data not found at {args.data}", file=sys.stderr)
        return 0

    prs = json.load(open(args.data))
    titles = [r["title"] for r in prs]
    if not titles:
        print("title-uniformity: no titles to check")
        return 0

    templates = [templatize(t) for t in titles]
    counts = Counter(templates)
    unique = len(counts)
    ratio = unique / len(titles)

    # Most-common template
    most_common, mc_count = counts.most_common(1)[0]

    if args.json:
        print(json.dumps({
            "total_titles": len(titles),
            "unique_templates": unique,
            "unique_ratio": ratio,
            "most_common_template": most_common,
            "most_common_count": mc_count,
            "threshold_ratio": args.min_unique_ratio,
        }, indent=2))
    else:
        if ratio < args.min_unique_ratio:
            print(f"title-uniformity: TOO UNIFORM — {unique}/{len(titles)} unique ({ratio:.0%}), threshold {args.min_unique_ratio:.0%}")
            print(f"  most common: \"{most_common}\" ({mc_count} PRs)")
            return 1
        else:
            print(f"title-uniformity: OK — {unique}/{len(titles)} unique ({ratio:.0%})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
