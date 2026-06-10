# OSS Agent Automation

> How the autonomous daily OSS contribution system works today, and where it is going next.

## Overview

The `oss-tracker` repository runs an autonomous agent that processes Hari's open-source PR backlog every day. It uses `opencode` in headless mode with **OpenCode Zen free tier** to analyze PRs and decide whether to nudge, fix, defer, or skip them.

The operating model is **autonomous execution with daily human review**. The agent should act like Hari would act after reading the PR context. Hari reviews the results after the run, but the system should not require approval before ordinary actions.

There are three planned workflows:

1. **Backlog Steward**: tracks existing PRs, follows up, fixes maintainer-requested issues, and raises local issues for human-only blockers like CLA.
2. **New Contributor**: finds and opens new OSS contributions, targeting 1-5 high-confidence PRs per day around 11 AM IST.
3. **Candidate Discovery**: refreshes the candidate pool from GitHub trends/search so `candidates.json` does not go stale.

## Architecture

```
GitHub Actions (cron: daily 9 AM UTC, cloud-hosted runner)
  ↓
Install opencode (via official install script)
  ↓
Configure Zen provider (free tier, no API key)
  ↓
Checkout oss-tracker
  ↓
Read .oss-harness/state/queue.json
  ↓
Select top 5 PRs
  ↓
For each PR:
  a. Fetch live data (gh pr view, comments, CI status)
  b. Run opencode run --format json
  c. Parse JSON decision
  d. Execute via gh CLI
  e. Log to progress.md
Commit and push
```

## Why GitHub Actions Cloud?

1. **Runs 24/7** — No need to keep your laptop on
2. **OpenCode Zen free tier** — zero cost, no API key required
3. **Always fresh** — Each run starts with a clean environment
4. **GitHub-native** — Uses GitHub Actions plus `GH_PAT` for cross-repo actions
5. **Visible** — Workflow runs show in GitHub Actions UI

## Daily Flow

Current implementation note: Backlog Steward now runs through `agent/backlog_steward.py`. `scripts/daily-batch.sh` is only a thin wrapper.

### 1. Queue Selection

The agent currently reads `queue.json` and picks the top 5 actionable PRs:
- A-tier first (mergeable, green CI, stale)
- Then B-tier with open review threads
- Then B-tier green but stale
- Skips: recently nudged (< 30 days), C-tier (conflicts), F-tier (archived)

Current limitation: the queue is **not advanced yet**. The same top items remain in place until a future state engine rotates, defers, or marks them done.

### 2. PR Analysis

For each PR, the agent:
1. Fetches live data via `gh pr view` (comments, reviews, CI status, files)
2. Constructs a structured prompt with all context
3. Sends it to `opencode` (Big Pickle via OpenCode Zen free tier)
4. Receives a JSON decision:
   ```json
   {
     "action": "nudge",
     "reason": "PR is mergeable, CI green, 108 days stale, no maintainer reply",
     "confidence": 8,
     "comment_body": "Hi! Quick status...",
     "requires_human": false
   }
   ```

### 3. Decision Execution

| Action | Behavior | Safety |
|---|---|---|
| **nudge** | Posts a comment via `gh pr comment` | Confidence threshold enforced |
| **fix** | Currently defers to human (future: checkout fork, push fix) | Never force-push upstream |
| **close** | Currently disabled by policy | Do not close PRs for now, keep nudging or defer |
| **skip** | Logs and moves on | Default for low confidence |
| **defer** | Logs for human attention | For rebase needed, CLA blocked, or ambiguous |

### 4. State Update

After processing:
- `progress.md` is appended with the batch results
- `actions.jsonl` records each action in machine-readable form
- `queue.json` is updated for cooldowns, rotations, and needs-attention items
- Changes are committed and pushed back to `oss-tracker`

## Safety Guardrails

The automation codifies the harness's hard rules:

1. **Batch size default**: 5 PRs per day through workflow input
2. **Confidence threshold**: Actions with confidence < 5 are converted to `defer`
3. **No force-push upstream**: All code pushes go to `Mr-Neutr0n/*` forks only
4. **Comment body required**: `nudge` actions require a non-empty comment
5. **One at a time**: Sequential processing within the batch
6. **No close for now**: Closing is not part of the autonomous policy yet
7. **Dry run mode**: `workflow_dispatch` supports `--dry-run` for testing
8. **Failure tracking**: Failed actions are logged in the workflow output and `progress.md`

Important: `requires_human` is informational in the current autonomous model. The desired behavior is not approval-gated execution. The right fix is better deterministic guardrails and better logging, not blocking every `requires_human` action.

Nudge policy target: nudge only when the latest comment is Hari's and at least 7 days have passed. If a maintainer or reviewer replied after Hari's last comment, the agent should reply to that message instead of posting a generic bump.

Implemented deterministic gates:

- `close` is blocked in code even if the model returns it.
- 7-day eligible nudges cannot be vetoed by the model, but opencode still writes the text.
- Red CI becomes `fix_needed` before any nudge.
- CLA/DCO blockers create local `oss-tracker` issues and move the PR to `needs_attention`.
- Repo preflight runs before posting comments.
- Comments are linted for length, banned phrases, bullet lists, close/closing offers, and em/en dashes.
- Public-facing text is opencode-authored. Deterministic code chooses actions, validates drafts, and routes failures, but does not write fallback upstream comments.

## Nandana's Innovations We Adopted

| Innovation | How We Use It |
|---|---|
| **GitHub Actions native** | Daily cron workflow, no local session needed |
| **Deterministic guardrails** | Confidence thresholds, auto-pilot for clear cases |
| **Dual-model architecture** | Future: `deepseek-v4-pro` for analysis, `qwen3p6-plus` for quick triage |
| **Hyper-fuzzy patching** | Future: automated fix generation with resilient patch matching |
| **Anti-loop forcing** | Max 3 attempts per PR; after 2 reads, force decision |
| **Auto-commit** | Future: verified fixes are committed and pushed to Hari's fork branches |

## What We Kept From Our Harness

| Feature | Why It Matters |
|---|---|
| **Sequential queue** | Prevents burst behavior, maintains contributor reputation |
| **Tier ratings** | Focuses effort on high-value PRs (A-tier = high stars, mergeable) |
| **Preflight checks** | Burst detect, title uniformity, secret blocklist |
| **Rich context** | `profiles/`, `progress.md`, `quality-bar.md` — narrative context |
| **Repo profiles** | Remember what's been said before, who maintainers are |
| **OSS-specific safety** | Never force-push upstream, never bulk-act, never forge CLA |

## Planned Workflow 2: New Contributor

The second workflow should run separately from backlog stewardship.

Target behavior:

1. Run daily around **11 AM IST** (`05:30 UTC`).
2. Select candidate repos and issues from local `.oss-harness/state/candidates.json` by score, not random order.
3. Refresh candidates through a separate discovery step that looks at trending/high-activity GitHub repos and GitHub issue search.
4. Prefer meaningful issues and bigger PRs when the agent can build enough confidence, not only tiny typo fixes.
5. Preflight every repo before cloning or commenting: archived, disabled, license, CONTRIBUTING, AI policy, CoC.
6. Fork the repo if needed, create a scoped branch, implement the smallest correct fix, run local checks if detectable, push to Hari's fork, and open a PR.
7. Record the new PR into the backlog queue so Backlog Steward owns follow-up from then on.
8. Stop after 1-5 successful high-confidence PRs. Do not force exactly 5 on low-quality days.
9. Comment or claim the source issue when appropriate, then open the PR when ready. Both messages must be professional, specific, and non-AI-like.

Candidate freshness:

- `candidates.json` is the working queue, not the source of all truth.
- A discovery job should add fresh repos/issues weekly or daily.
- The New Contributor job consumes the highest-scored ready candidates.
- Processed candidates move to `attempted`, `contributed`, `skipped`, or `failed` so they are not retried blindly.
- New candidates are deduplicated by `repo` and `repo#issue`.

Borrowed from `nandanadileep/automate-opensource`:

- Batch through candidate projects until at least one contribution lands.
- Detect existing PRs for the same issue before opening a duplicate.
- Use patch-format guardrails for generated edits.
- Run simple local test probes before pushing.
- Mark candidates as contributed, reviewed, skipped, or failed.

Changes for this harness:

- Use `opencode` CLI with Zen free tier instead of an OpenAI-compatible API key.
- Use local `.oss-harness` state instead of Notion.
- Enforce OSS reputation limits: max new PRs per repo, no bursty titles, no AI-banned repos, no mass issue claiming.
- Allow up to 30 open PRs per upstream repo across both workflows.
- Feed every opened PR into the existing tracking queue.

## Planned Workflow 3: Candidate Discovery

The candidate pool should not depend on manual updates forever.

Target behavior:

1. Run daily or weekly before New Contributor.
2. Find high-activity repositories using GitHub search and optionally web search.
3. Add the top 10 fresh repos from the week after deduplication and preflight.
4. For each repo, find candidate issues using labels like `good first issue`, `help wanted`, `bug`, `regression`, `test`, and `docs`.
5. Score each candidate by repo quality, issue quality, maintainer responsiveness, scope, and likelihood of local verification.
6. Write only scored candidates into `.oss-harness/state/candidates.json`.
7. Never open PRs directly from discovery. Discovery only fills the queue.

Initial discovery sources:

- GitHub Search API: recently updated repos by stars, language, and pushed date.
- GitHub issue search: open issues with useful labels and recent activity.
- Optional web search: trending GitHub repos for the week, then verify everything through GitHub API.

EXA or another web search provider is optional. The safer first version can use GitHub API plus normal web search, because GitHub API gives structured dedupable data.

## Setup

### 1. Enable GitHub Actions

In your repository settings, make sure GitHub Actions is enabled:
https://github.com/Mr-Neutr0n/oss-tracker/settings/actions

### 2. Verify `GH_PAT` Permissions

The workflow uses `GH_PAT` for cross-repo comments, fork pushes, and pushing state back to `oss-tracker`. The default `GITHUB_TOKEN` is not enough for external repository actions.

### 3. Trigger a Test Run

Go to:
https://github.com/Mr-Neutr0n/oss-tracker/actions/workflows/daily-oss-agent.yml

Click **Run workflow** → set `dry_run: true` → **Run**.

## Monitoring

- **Workflow runs**: https://github.com/Mr-Neutr0n/oss-tracker/actions
- **Progress log**: `.oss-harness/state/progress.md`
- **Queue status**: `.oss-harness/state/queue.json`
- **Failure issues**: Automatically created in `oss-tracker` if a batch fails

## Cost

- **OpenCode Zen**: Free tier (100 requests/day) — 5 PRs/day uses ~5 requests
- **GitHub Actions**: Ubuntu runner usage counts against the private repo's Actions allowance
- **Total**: $0

## Troubleshooting

| Issue | Solution |
|---|---|
| opencode install fails | Check network connectivity; retry workflow |
| Zen model not found | Verify `opencode.json` is correctly written in workflow |
| PR analysis fails | Check `GITHUB_TOKEN` permissions; ensure `repo` scope is granted |
| Batch creates no actions | Queue may be empty or all PRs recently nudged |
| Rate limit exceeded | Zen free tier allows 100 requests/day; lower `batch_size` on the next manual run |

## Future Enhancements

1. **State engine**: Rotate queue items, enforce cooldowns, and maintain `done`, `deferred`, and `needs_attention` lists.
2. **Deterministic preflight**: Run repo checks before any action.
3. **CI parser**: Fetch check names, required checks, logs, and likely root cause.
4. **Automated fixes**: Checkout Hari's fork branch, apply opencode patch, run tests, push to fork.
5. **New Contributor workflow**: Open 1-5 high-confidence PRs per day from vetted candidate issues.
6. **Candidate Discovery workflow**: Add fresh high-scoring repos and issues to the local candidate queue.
7. **CLA issue creation**: When a PR is blocked on CLA/DCO, open an `oss-tracker` issue for Hari and stop touching that PR until resolved.
8. **Repo memory**: Track maintainer behavior, repo responsiveness, and accepted PR patterns.

## License

Same as the harness. This is Hari's personal automation.
