#!/usr/bin/env python3
"""
Repo pre-flight scanner.

Before the harness engages with any external repo, run this. It checks:
  - isArchived → block
  - isDisabled → block
  - licenseInfo.spdxId → warn if missing
  - pushedAt → warn if > 1 year stale
  - has SECURITY.md → log
  - has CONTRIBUTING.md → log; if contains 'no AI', 'no AI-assisted', or
    'AI-generated PRs will be closed' → block and surface
  - has CODEOWNERS → log
  - has AI policy / .github/AI_POLICY.md → fetch and surface

Usage:
    repo-preflight.py OWNER/REPO [--json]

Exit codes:
    0  OK
    1  blocked (archived / disabled / AI ban)
    2  warn (license missing / repo stale)
"""
import argparse, json, subprocess, sys, re
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "state" / "preflight_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def parse_pushed(s):
    if not s: return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repo")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    code, out, err = gh(["api", f"repos/{args.repo}"])
    if code != 0:
        print(f"repo-preflight: failed to fetch {args.repo}: {err.strip()}", file=sys.stderr)
        return 2

    repo = json.loads(out)
    findings = {
        "repo": args.repo,
        "isArchived": repo.get("archived"),
        "isDisabled": repo.get("disabled"),
        "license": (repo.get("license") or {}).get("spdx_id"),
        "pushedAt": repo.get("pushed_at"),
        "stars": repo.get("stargazers_count"),
        "defaultBranch": repo.get("default_branch"),
    }

    block = False
    warn = False
    notes = []

    if repo.get("archived"):
        block = True
        notes.append("BLOCK: repo is archived")
    if repo.get("disabled"):
        block = True
        notes.append("BLOCK: repo is disabled")
    if not (repo.get("license") or {}).get("spdx_id"):
        warn = True
        notes.append("WARN: no license (spdx_id missing)")
    pushed = parse_pushed(repo.get("pushed_at"))
    if pushed:
        age_days = (datetime.now(timezone.utc) - pushed).days
        findings["pushedAgeDays"] = age_days
        if age_days > 365:
            warn = True
            notes.append(f"WARN: no push in {age_days} days (repo stale)")

    # Check CONTRIBUTING.md for AI ban
    for path in ["CONTRIBUTING.md", "docs/CONTRIBUTING.md", ".github/CONTRIBUTING.md"]:
        code2, out2, _ = gh(["api", f"repos/{args.repo}/contents/{path}"])
        if code2 == 0:
            import base64
            j = json.loads(out2)
            content = base64.b64decode(j.get("content", "")).decode("utf-8", errors="replace").lower()
            if any(p in content for p in [
                "no ai", "no ai-assisted", "no ai generated", "no ai-generated",
                "ai-generated prs will be closed", "no automated prs",
                "no bot prs", "no llm",
            ]):
                block = True
                notes.append(f"BLOCK: AI ban found in {path}")
            else:
                notes.append(f"info: CONTRIBUTING found at {path}, no AI ban detected")
            break

    # Check explicit AI policy
    code3, out3, _ = gh(["api", f"repos/{args.repo}/contents/.github/AI_POLICY.md"])
    if code3 == 0:
        notes.append("info: .github/AI_POLICY.md exists — read it before commenting")
        import base64
        content = base64.b64decode(json.loads(out3).get("content", "")).decode("utf-8", errors="replace")
        if "no" in content.lower()[:200] and "ai" in content.lower()[:400]:
            block = True
            notes.append("BLOCK: .github/AI_POLICY.md appears to prohibit AI-assisted PRs")

    findings["notes"] = notes
    findings["block"] = block
    findings["warn"] = warn

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        print(f"repo-preflight: {args.repo}")
        for k in ("isArchived", "isDisabled", "license", "stars", "pushedAgeDays"):
            if k in findings:
                print(f"  {k}: {findings[k]}")
        for n in notes:
            print(f"  • {n}")

    if block: return 1
    if warn: return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
