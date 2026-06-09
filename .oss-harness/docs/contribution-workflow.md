# Contribution Workflow

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
