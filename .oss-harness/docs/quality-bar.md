# Quality Bar

What "good" looks like for a Hari PR. Use this as the rubric for self-review and for the agent harness.

## For a new PR (before opening)

- [ ] Title is **specific** — names the file or function, not "fix bug"
- [ ] Title is **unique** in the repo's recent PR history (not "fix: bug" if there are five other "fix: bug" PRs)
- [ ] Body has a one-line summary, the root cause in 1–2 sentences, and a one-line test/verification
- [ ] Diff is small and on-topic — no incidental reformatting
- [ ] CI is green on the push that becomes the PR
- [ ] `Signed-off-by:` trailer if the project uses DCO
- [ ] Linked to the issue it fixes (if there is one)
- [ ] No `print(...)` left in the diff
- [ ] No `TODO` introduced unless the diff's purpose is the TODO

## For an existing PR (before nudging)

- [ ] PR still applies to the latest main (no conflicts)
- [ ] All CI checks have a green/red status, no "pending" older than 24h
- [ ] You have read every comment, including bot comments
- [ ] The most recent maintainer message is older than 14 days (don't nudge faster)
- [ ] You have something to say — not "still interested?" but a concrete status
- [ ] If the diff is more than 6 months stale, the fix is probably obsolete — close it

## Voice for PR comments

- **One paragraph maximum.** Maintainers are busy.
- **No "any update?"** Pressure phrasing gets muted or marked as spam.
- **No "just bumping this"** Same reason.
- **Concrete:** "rebased on latest main, all 142 tests pass locally, CI re-running" — not "updated."
- **Honest:** "this is probably obsolete given the new pipeline, happy to close" — not "still relevant."
- **No marketing.** No "this is a great project, I love what you're doing." Maintainers are not here for your review of their work.

## What gets a PR closed by the harness

- The repo is archived (per `block_archived_repos: true`)
- It's a fork Hari owns (per `exclude_owners`)
- `daily_comment_cap` is reached for the day
- The PR has been open > 180 days with no engagement
- The user explicitly says "close it"
- The maintainer has said "stop" or closed it themselves

## What does NOT get a PR closed by the harness

- It has an active review thread. (You respond, you don't close.)
- It's been < 60 days. (You wait.)
- The fix is "small" and the project is healthy. (You nudge once.)
- You don't have time today. (You leave it for tomorrow.)
