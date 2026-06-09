#!/usr/bin/env python3
"""
Secret / token / key blocklist.

Scans a file (or stdin) for any of the patterns in secret-patterns.txt.
If any pattern matches, prints a single-line alert per match and exits 1.

Usage:
    secret-blocklist.py PATH [PATH ...]
    cat file | secret-blocklist.py -

Exit codes:
    0  clean
    1  secret-like string found
"""
import re, sys
from pathlib import Path

PATTERNS_FILE = Path(__file__).parent / "secret-patterns.txt"


def load_patterns():
    pats = []
    for line in PATTERNS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pats.append(re.compile(line))
    return pats


def scan_text(text, source, patterns):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in patterns:
            m = pat.search(line)
            if m:
                findings.append((source, i, m.group(0)[:30] + ("..." if len(m.group(0)) > 30 else "")))
    return findings


def main():
    if len(sys.argv) < 2:
        print("usage: secret-blocklist.py PATH [PATH ...]   (or '-' for stdin)", file=sys.stderr)
        return 2

    patterns = load_patterns()
    targets = sys.argv[1:]

    all_findings = []
    for t in targets:
        if t == "-":
            text = sys.stdin.read()
            all_findings += scan_text(text, "<stdin>", patterns)
        else:
            try:
                text = Path(t).read_text(errors="replace")
                all_findings += scan_text(text, t, patterns)
            except Exception as e:
                print(f"  skip {t}: {e}", file=sys.stderr)

    if all_findings:
        print(f"secret-blocklist: {len(all_findings)} secret-like string(s) found:")
        for src, line, snippet in all_findings:
            print(f"  {src}:{line}  →  {snippet}")
        return 1
    else:
        print("secret-blocklist: clean — no secret-like strings")
        return 0


if __name__ == "__main__":
    sys.exit(main())
