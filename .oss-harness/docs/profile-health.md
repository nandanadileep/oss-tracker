# Profile Health

A snapshot of how Hari's contributor profile looks to a maintainer scanning it. This is the document to read before opening a new PR or sending a wave of comments.

## The 12 flags (current state, 2026-06-01)

| # | Flag | Reading | Action |
|---|---|---|---|
| 1 | Volume | 218 open + 155 closed across 233 repos | High but spread out |
| 2 | Merge rate | 47% (73/155 decided) | OK, not great |
| 3 | Open backlog | 218, 0 drafted, 203 are 90+ days stale | Maintenance emergency |
| 4 | Maintainer engagement | 109/218 have **zero** comments or reviews | Most will auto-close |
| 5 | Conflicts | 7 PRs CONFLICTING | Rebase these first |
| 6 | Wasted effort | 3 PRs in archived repos | Close and forget |
| 7 | Positive signal | 2 APPROVED, 18 with open threads | Real work is landing |
| 8 | Burst pattern | ~30+ PRs in 5 days (Feb 11–15 2026) | **Maintainer-visible red flag** |
| 9 | Stale bot risk | 200+ PRs will be auto-closed in 30d | Be ready to accept loss |
| 10 | Title uniformity | Many PRs use identical phrasings | Vary future titles |
| 11 | High-value repos | transformers, diffusers, pytorch, lightning, autogen | Right neighborhood |
| 12 | Diverse languages | Python-heavy, with JS, Rust, Go, C++ | Good range |

## What this means going forward

### For the next 7 days

- Close the 3 archived-repo PRs.
- Rebase + push the 7 CONFLICTING PRs.
- Read every thread on the 18 with open review threads, respond to each.
- Post a single one-line `status_update` comment on the 27 Tier-A PRs.

That's ~55 PR actions, but most are close-and-forget, not new work. The point is to clean the slate.

### For the next 30 days

- Pick **at most** 2 repos to focus on. Hit them hard, land something. Build the maintainer relationship, not just the volume.
- Set a personal rule: **at most 1 new PR per week** to any repo, no matter how many bugs you find.
- Vary your titles. "Fix mutable default argument in X" reads like a template.

### For the next 90 days

- Goal: merge 5 PRs.
- Goal: get 3 of the existing 18 threaded PRs to a maintainer-approved state.
- Goal: clean the open backlog to < 50.

### The do-not-list

- Do not open 30+ PRs in a week again. Even if they're "all real fixes." A maintainer's mental model of you is shaped by the burst.
- Do not bulk-nudge. Even with polite templates. One PR at a time.
- Do not post "still interested?" comments. The templates don't include that phrase for a reason.

## How to read profile health

When the harness reports a flag is amber, that's an opportunity, not a failure. When the flag is red, that's a behavior change needed. When the flag is green, hold the course.

| Color | Meaning |
|---|---|
| 🟢 Green | Behavior matches a good contributor profile. Continue. |
| 🟡 Amber | Sub-optimal but not damaging. Improve gradually. |
| 🔴 Red | Damaging. Change behavior this week. |

Today's colors: 🟡 1, 4, 9, 10. 🔴 3, 6, 8. 🟢 2, 5, 7, 11, 12. (Out of 12.)
