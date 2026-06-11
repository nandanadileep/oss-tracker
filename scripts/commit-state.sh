#!/usr/bin/env bash
# Commit harness state back to main. Runs `if: always()` so even failed runs
# persist their ledger (crash-only design, DOMAIN_MODEL.md §10).
# Safe under races: union-merge for append-only files (.gitattributes),
# pull --rebase with retries for everything else.
set -euo pipefail

LABEL="${1:-run}"

git config user.email "nandanadileep29@gmail.com"
git config user.name "nandanadileep"
if [ -n "${GH_PAT:-}" ]; then
  git remote set-url origin "https://x-access-token:${GH_PAT}@github.com/${GITHUB_REPOSITORY:-nandanadileep/oss-tracker}.git"
fi

git add -A .oss-harness/state/ .oss-harness/reports/ 2>/dev/null || true
if git diff --cached --quiet; then
  echo "no state changes to commit"
  exit 0
fi
git commit -m "state: ${LABEL} $(date -u +%Y-%m-%dT%H:%MZ)"

for attempt in 1 2 3; do
  if git push origin HEAD:main; then
    echo "state pushed (attempt ${attempt})"
    exit 0
  fi
  echo "push rejected; rebasing (attempt ${attempt})"
  git fetch origin main
  # union merge drivers in .gitattributes resolve ledger/progress automatically
  git rebase origin/main || { git rebase --abort; git pull --rebase=false origin main --no-edit; }
  sleep $((attempt * 5))
done
echo "::error::could not push state after 3 attempts"
exit 1
