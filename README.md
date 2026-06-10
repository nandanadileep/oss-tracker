# oss — Open-Source Contribution Harness for @Mr-Neutr0n (Hari)

This folder is the **single source of truth** for Hari's open-source contribution work — a place where the 218 open PRs, 155 closed PRs, 233 external repos, the maintainer relationships, the lint rules that keep new contributions clean, and the security scanners that pre-flight any code we read, all live together.

It is not a product. It is a **harness for working on OSS contributions**, deliberately small.

## What it does

1. **Inventory** — pulls every PR Hari has authored on external OSS repos and rates it (engagement, conflicts, freshness, tier).
2. **Reports** — renders the inventory into a human-readable markdown report.
3. **Lint** — flags the patterns that hurt Hari's contributor profile (mass-burst, title uniformity, secret-like patterns).
4. **Scan** — pre-flights any new repo / file we read for archived status, leaked secrets, missing license.
5. **Queue** — keeps a single sequential work queue for the next PR to engage with. No scripts that touch PRs without per-PR human-or-agent review.
6. **Progress log** — every action the harness takes is appended to a `progress.md` file, so future sessions can pick up without losing context (Anthropic "claude-progress.txt" pattern).
7. **Automated workflows** — three GitHub Actions workflows run on a schedule:
   - **Backlog Steward** — maintains existing PRs (nudge, reply, fix, close)
   - **New Contributor** — opens new PRs on discovered issues
   - **Candidate Discovery** — finds new repos and issues to contribute to

## Top-level layout

```
oss/
├── AGENTS.md                  ← AI-agent entry point. Read this first.
├── README.md                  ← This file.
├── VERSION                    ← 0.1.0
├── CHANGELOG.md               ← Harness release notes
└── .oss-harness/
    ├── config.json            ← User, scopes, rate limits, hard stops.
    ├── data/                  ← Raw GitHub API responses + the rated PR list.
    ├── docs/                  ← Architecture, workflow, quality bar, profile health, security.
    ├── lint/                  ← Burst detector, title-uniformity, secret-pattern checks.
    ├── scanners/              ← Repo pre-flight: archived, license, secret scan, CoC awareness.
    ├── state/                 ← queue.json + progress.md (sequential, durable).
    ├── profiles/              ← Per-repo notes (populated as Hari engages with each repo).
    └── reports/               ← Generated reports.
```

## First-time use

```bash
# 1. Make sure gh is authenticated
gh auth status

# 2. (Optional) Re-pull the PR inventory
gh search prs --author @me --state all --json ... > .oss-harness/data/prs_open.json

# 3. Read the report
cat .oss-harness/reports/PR_REPORT.md

# 4. Read the agent entry point
cat AGENTS.md
```

## Operating principles

These are the rules the harness (and any agent acting on Hari's behalf) must follow. They are not negotiable; they are how a good open-source citizen behaves.

1. **One PR at a time.** No bulk-comment scripts. No auto-push. Read each PR's comments, check its CI, then act.
2. **Honest reports.** If a PR is in an archived repo, say so and close it. Don't mass-nudge dead work.
3. **Status updates, not pressure.** Comments to maintainers state what's changed since the last activity, ask for nothing more than feedback.
4. **DCO / CLA / sign-off always honored.** Never forge. Never bypass.
5. **No `git push --force` to upstream PR branches.** Ever.
6. **Secrets never enter the harness.** Token, key, password patterns in any file = immediate alert.
7. **Hard stops are in `.oss-harness/config.json`** — `daily_comment_cap`, `max_open_per_repo`, `cooldown_after_close_days`, `burst_window_max_prs`.

## References (read, do not clone)

Patterns this harness borrows from:

- [Anthropic — Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) (init + progress + JSON plans)
- [celesteanders/harness](https://github.com/celesteanders/harness) (generator + evaluator, JSON plans)
- [hashwnath/harnesskit](https://github.com/hashwnath/harnesskit) (universal AGENTS.md scaffold, exec-plans)
- [tessl-labs/good-oss-citizen](https://tessl.io/registry/tessl-labs/good-oss-citizen) (rules for AI agents contributing to OSS)
- [JaviMaligno/oss-agent](https://github.com/JaviMaligno/oss-agent) (exact same problem space — automated OSS contributions)
- [chiruu12/OSS-Skills](https://github.com/chiruu12/OSS-Skills) (Claude Code skills for OSS contributions)
- [KwokJay/cc-agent-harness](https://github.com/KwokJay/cc-agent-harness) (verifier + manifest + export loop)

These are not vendored. They're pattern references. If you want a feature that one of them does well, copy the pattern, not the code.
