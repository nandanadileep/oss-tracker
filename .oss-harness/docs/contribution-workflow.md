# Contribution Workflow

## Automated Workflows

Three GitHub Actions workflows run on a schedule:

| Workflow | File | Purpose | Schedule |
|----------|------|---------|----------|
| **Backlog Steward** | `.github/workflows/daily-oss-agent.yml` | Maintains existing PRs: nudge, reply, fix, close | Daily at 9 AM UTC |
| **New Contributor** | `.github/workflows/daily-new-contributions.yml` | Opens new PRs on discovered issues | Daily at 5:30 AM UTC |
| **Candidate Discovery** | `.github/workflows/discover-candidates.yml` | Discovers new repos and issues | Weekly on Sunday at 8 AM UTC |

### Backlog Steward
- Processes existing PRs from `queue.json`
- Default batch size: 25 PRs per run
- Actions: nudge (polite follow-up), reply (to maintainer), fix (apply code changes), close, skip
- When CI is failing, it attempts to generate a fix patch, apply it, verify with tests, and push to the PR branch
- When a maintainer asks for changes, it replies with the requested updates
- **Fix limit:** Maximum 5 fix operations per run (expensive: clone + patch + test + push). After 5 fixes, remaining fix items are deferred to the next run.
- Stops processing when batch size is reached or no more actionable items

### New Contributor
- Consumes `candidates.json` (populated by Candidate Discovery)
- Selects high-confidence candidates (score > 7, not recently tried)
- For each candidate: forks repo, creates branch, generates fix with LLM, applies patch, verifies tests, pushes, opens PR
- Stops after first successful PR (not all candidates)
- Max 5 PRs per day, min 1

### Candidate Discovery
- Searches GitHub for active repos (stars > 1000, recently pushed)
- Finds open issues with labels like "good first issue", "help wanted", "bug"
- Scores candidates by: repo stars, issue activity, language match, recency
- Deduplicates against existing candidates and recent contributions
- Writes to `candidates.json` for New Contributor to consume

## Daily loop

The harness assumes the user opens the folder, looks at the report, picks the next PR from the queue, and acts. The loop is:

```
1. Read  .oss-harness/state/progress.md           (what did the last session do?)
2. Read  .oss-harness/reports/PR_REPORT.md         (what's the backlog shape today?)
3. Pop   next item from .oss-harness/state/queue.json
4. For that PR, sequentially:
     a. gh pr view <repo>#<n> --json state,mergeable,reviewDecision
     b. gh pr view <repo>#<n> --comments           (read every comment)
     c. gh run list --repo <repo> --pr <n>         (check CI)
     d. Decide: nudge / rebase+push / close / respond / leave
     e. If acting on the PR: run linters first, run repo-preflight first
     f. Comment with one of the templates from config.json
     g. Append one bullet to progress.md
     h. If the PR is now closed, remove it from queue.json
5. Stop after `daily_comment_cap` comments. The rest can wait for tomorrow.
```

## The decision tree

```
                ┌─ archived? ──── yes ─→ close with archived template
                │
                ├─ conflict? ──── yes ─→ rebase + push + re-request review
                │
                ├─ CHANGES_REQUESTED? ── yes ─→ read review, fix, push
                │
                ├─ REVIEW_REQUIRED with no comment > 60d? ── yes ─→ nudge
                │
                ├─ open thread with new maintainer msg > 14d? ── yes ─→ respond
                │
                └─ nothing? ──→ do not touch. Stale bots will close it.
```

The exact threshold numbers live in `config.json`. The decision tree itself lives in this file because it documents intent, not config.

## What "thoroughly check" means for one PR

The user has stated: *"you need to go through the comments in the PR, you need to look at the GitHub actions in the PR."* For each PR, before posting anything, the harness will:

1. **`gh pr view <repo>#<n> --json title,body,state,mergeable,reviewDecision,isDraft,additions,deletions,changedFiles,headRefName,baseRefName,author,labels`** — the body tells you if the original PR description is still accurate. The `additions/deletions` gives you a sense of effort. `mergeable` tells you if CI is unhappy or there are conflicts.
2. **`gh pr view <repo>#<n> --comments`** — read every comment, including the bot ones. Bot comments often tell you what humans won't: "label: needs-author-feedback", "merge conflict detected on base", "stale in 7 days".
3. **`gh run list --repo <repo> --pull-request <n> --json status,conclusion,name,headBranch,event,createdAt,updatedAt,databaseId`** — for each CI run on the PR, what was the conclusion? Failing tests? Lint errors? If any run failed, you should know which test and why.
4. **`gh api repos/<repo>/contents/CONTRIBUTING.md`** (if it exists) — to be sure your comment doesn't violate a project rule (some projects ask you not to comment, or only to comment once).
5. **`gh api repos/<repo>`** — `archived`, `disabled`, `pushedAt` (how recently the repo moved).

If anything in the above suggests the PR is dead, archived, or the project has explicitly asked for no more activity, the harness does NOT post.

## Comment templates

Six templates live in `config.json` under `tone.templates`. Use them by key, not by writing ad-hoc text:

- `status_update` — the polite nudge. "Still applies, still interested."
- `chase_with_change` — after you actually pushed a new commit. "Pushed an update, re-requesting review."
- `close_stale` — when you're withdrawing. "No activity in N days, closing. Happy to reopen."
- `close_archived` — repo is archived.
- `respond_to_review` — when a maintainer asked for changes and you addressed them.
- `respond_to_conflict` — after a rebase.

Any text posted to a PR MUST be one of these templates with the placeholders filled in. No ad-hoc text. This is to keep the voice consistent and to keep the user from accidentally pressuring a maintainer.

## "Do this sequentially, don't create scripts"

This is a hard constraint from the user. Concretely:

- No `for pr in $(cat queue.json); do gh pr comment ...; done`.
- No `comment-all.sh`.
- No "if comment count == 0 and stale > 60d then post template X" loop.

The harness *runs* lint and scanner scripts because those are deterministic and read-only. But every write to GitHub — comments, pushes, closes, reopens — is one PR at a time, decided by the human or by an agent in a single-session loop that re-reads the PR between every action.

If you find yourself wanting to write a loop, **stop and ask the human**.
