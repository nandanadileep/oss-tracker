# Architecture

## Purpose

The harness is a single-machine, single-user, single-purpose system: it helps Hari (a single human with one GitHub account) **stay a good open-source citizen** across the ~233 external repos he has touched.

It is not a SaaS, not a multi-tenant tool, not a CI bot. It's a notebook with structure.

## Layers

```
┌─────────────────────────────────────────────────────────────────┐
│ AGENTS.md / README.md   ← human and AI entry points             │
├─────────────────────────────────────────────────────────────────┤
│ .oss-harness/docs/      ← knowledge (what good looks like)      │
│ .oss-harness/profiles/  ← per-repo memory (what was said)       │
├─────────────────────────────────────────────────────────────────┤
│ .oss-harness/lint/      ← static checks before any action      │
│ .oss-harness/scanners/  ← pre-flight on every external read    │
├─────────────────────────────────────────────────────────────────┤
│ .oss-harness/data/      ← raw API responses (immutable)         │
│ .oss-harness/reports/   ← derived views of data/                │
├─────────────────────────────────────────────────────────────────┤
│ .oss-harness/state/     ← queue.json + progress.md (durable)    │
└─────────────────────────────────────────────────────────────────┘
```

The rule is: **data flows down, knowledge flows sideways, state lives at the bottom.** A lint failure stops the action; a profile note changes the comment; a state file is never overwritten, only appended.

## Data model

- `data/prs_open.json` — every PR Hari has authored, in the `OPEN` state, on any repo. Pulled via `gh search prs --author @me --state open`.
- `data/prs_closed.json` — same, but `CLOSED` (includes both `merged` and `closed`).
- `data/oss_open.json`, `data/oss_closed.json` — same data, filtered to exclude Hari's own forks.
- `data/pr_enriched.json` — GraphQL-enriched with review state, CI rollup, comments, threads, labels.
- `data/prs_rated.json` — the per-PR rating: tier, label, stars, staleness, etc.
- `data/PR_REPORT.md` — the human-readable report, derived from the rated list.

## State model

- `state/queue.json` — the ordered list of `repo#number` strings for the next PR to engage with. One item gets popped, acted on, then the next advances. Re-population happens by a (manual) refresh of the rated list and a sort by tier/priority.
- `state/progress.md` — append-only session log. Every action the harness takes is one bullet under the date. Sessions restart by reading this file first.

## The "one PR at a time" rule, formalized

The harness has no mechanism to bulk-comment. There is no `comment-all.sh`. There is no `--all` flag on the comment template. To comment on 100 PRs, you run the comment action 100 times, each time re-reading the PR's current state.

This is by design, and the user has stated it explicitly. The cost of this is speed; the benefit is that no PR gets a comment that wasn't read.

## Lint and scanner contracts

Lint and scanner scripts:

- Live in `.oss-harness/lint/` and `.oss-harness/scanners/`.
- Take a target via argv or stdin (e.g. `lint/burst-detect.py --window 24h`).
- Return exit code 0 (pass), 1 (fail), 2 (warn). The harness reads the exit code, not the prose.
- Print a single-line verdict, optionally with a JSON block in `--json` mode.

A lint failure means "don't act, fix the underlying issue first."

## Versioning

Semver-ish on `VERSION`:

- **Patch** (0.0.x) — typo / link / formatting fixes.
- **Minor** (0.x.0) — new lint rule, new doc, new scanner, new report section.
- **Major** (x.0.0) — break in the data model, queue format, or hard rules.

Every release gets a `CHANGELOG.md` entry with the date and a short list.

## What this harness is NOT

- Not a code review bot. It does not post to PRs it didn't author.
- Not an issue triager. It does not auto-respond to issues.
- Not autonomous. Every action that touches GitHub is gated on a human or a carefully-scoped agent session.
- Not a multi-tenant service. It has one user. The user is Hari.
