# AGENTS.md — Entry point for any AI agent working in this folder

> **Read this first.** It's intentionally short. It tells you what the folder is, where to look, and what you must never do.

## What this folder is

`oss/` is **your personal open-source contribution harness**. It tracks your open PRs and closed PRs across external repos. It is not a product. It is a control room for staying a good OSS citizen.

## Where to look

| If you want to… | Read |
|---|---|
| Understand the user's contribution posture | `.oss-harness/docs/profile-health.md` |
| Understand the harness architecture | `.oss-harness/docs/architecture.md` |
| Understand the daily workflow (triage, respond, close) | `.oss-harness/docs/contribution-workflow.md` |
| Understand what "good" looks like in a PR | `.oss-harness/docs/quality-bar.md` |
| Know the security rules | `.oss-harness/docs/security-policy.md` |
| See the current backlog, rated | `.oss-harness/reports/PR_REPORT.md` |
| See what was done last session | `.oss-harness/state/progress.md` |
| See the next PR queued for action | `.oss-harness/state/queue.json` |
| Inspect a specific PR's raw data | `.oss-harness/data/prs_rated.json` |
| Inspect the GitHub API enrich | `.oss-harness/data/pr_enriched.json` |

## Hard rules (non-negotiable)

1. **Never bulk-act on PRs.** One PR at a time. Read the comments, check the CI, decide, then act.
2. **Never use a script to post a comment.** Comments go out one at a time, after the human or agent has actually read the PR.
3. **Never push `--force` to a PR branch on an upstream repo.**
4. **Never forge DCO sign-off or CLA confirmation.**
5. **Never include a token, API key, or secret pattern in any file in this folder.** If you find one, surface it and stop.
6. **Never comment on a PR whose repo is archived.** Close it instead.
7. **Never re-open or un-close a PR** without the human's explicit instruction.
8. **Honor the limits in `.oss-harness/config.json`:** `daily_comment_cap`, `max_open_per_repo`, `cooldown_after_close_days`, `burst_window_max_prs`.
9. **If a maintainer says "stop" or closes the PR, you stop.** Don't keep nudging.
10. **Always append to `state/progress.md` after every action.** Future sessions will read it.

## Linters and scanners (run before any action)

- `.oss-harness/lint/burst-detect.py` — flags when too many PRs land in too short a window.
- `.oss-harness/lint/title-uniformity.py` — flags when PR titles follow a single template.
- `.oss-harness/lint/secret-blocklist.py` — scans any new file for token / key patterns.
- `.oss-harness/scanners/repo-preflight.py <owner/repo>` — checks archived, license, CoC, before engaging.

## Sequential queue

PR engagement is in `.oss-harness/state/queue.json`. The format is: ordered list of `repo#number`. The agent processes one, then advances. **Do not parallelize. Do not skip. Do not reorder without instruction.**

## Repo profiles

For each repo Hari has multiple open PRs with, there's a notes file in `.oss-harness/profiles/`. Read it before commenting — the file remembers what's been said before, who the maintainers are, what the project's CoC says.

## If you're not sure

Default to: **don't act, ask the human.** Hari is in the loop. The harness is not autonomous and is not meant to be.
