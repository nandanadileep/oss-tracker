# OSS Agent Automation

> How the autonomous daily OSS contribution agent works.

## Overview

The `oss-tracker` repository now runs an autonomous agent that processes your open-source PR backlog every day. It uses `opencode` in headless mode with **OpenCode Zen free tier** (zero cost) to analyze PRs and decide whether to nudge, fix, close, or skip them.

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
  ↓
Update queue.json
  ↓
Commit and push
```

## Why GitHub Actions Cloud?

1. **Runs 24/7** — No need to keep your laptop on
2. **OpenCode Zen free tier** — zero cost, no API key required
3. **Always fresh** — Each run starts with a clean environment
4. **GitHub-native** — Uses `GITHUB_TOKEN` for all operations
5. **Visible** — Workflow runs show in GitHub Actions UI

## Daily Flow

### 1. Queue Selection

The agent reads `queue.json` and picks the top 5 actionable PRs:
- A-tier first (mergeable, green CI, stale)
- Then B-tier with open review threads
- Then B-tier green but stale
- Skips: recently nudged (< 30 days), C-tier (conflicts), F-tier (archived)

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
| **nudge** | Posts a comment via `gh pr comment` | Only if confidence ≥ 5 and no recent nudge |
| **fix** | Currently defers to human (future: checkout fork, push fix) | Never force-push upstream |
| **close** | Posts comment, then closes via `gh pr close` | Only with explicit reason and comment body |
| **skip** | Logs and moves on | Default for low confidence |
| **defer** | Logs for human attention | For rebase needed, CLA blocked, or ambiguous |

### 4. State Update

After processing:
- `progress.md` is appended with the batch results
- `queue.json` is updated (advance, mark done, defer)
- Changes are committed and pushed back to `oss-tracker`

## Safety Guardrails

The automation codifies the harness's hard rules:

1. **Batch size limit**: 5 PRs per day (configurable, but capped at 10)
2. **Confidence threshold**: Actions with confidence < 5 are converted to `defer`
3. **No force-push**: All code pushes go to `Mr-Neutr0n/*` forks only
4. **Comment body required**: `close` and `nudge` actions require a non-empty comment
5. **One at a time**: Sequential processing within the batch
6. **No re-open**: Close actions are permanent; no undo
7. **Dry run mode**: `workflow_dispatch` supports `--dry-run` for testing
8. **Failure tracking**: Failed actions create a GitHub Issue in `oss-tracker`

## Nandana's Innovations We Adopted

| Innovation | How We Use It |
|---|---|
| **GitHub Actions native** | Daily cron workflow, no local session needed |
| **Deterministic guardrails** | Confidence thresholds, auto-pilot for clear cases |
| **Dual-model architecture** | Future: `deepseek-v4-pro` for analysis, `qwen3p6-plus` for quick triage |
| **Hyper-fuzzy patching** | Future: automated fix generation with resilient patch matching |
| **Anti-loop forcing** | Max 3 attempts per PR; after 2 reads, force decision |
| **Auto-commit** | Future: verified fixes are auto-committed with `[ci-auto-fix]` |

## What We Kept From Our Harness

| Feature | Why It Matters |
|---|---|
| **Sequential queue** | Prevents burst behavior, maintains contributor reputation |
| **Tier ratings** | Focuses effort on high-value PRs (A-tier = high stars, mergeable) |
| **Preflight checks** | Burst detect, title uniformity, secret blocklist |
| **Rich context** | `profiles/`, `progress.md`, `quality-bar.md` — narrative context |
| **Repo profiles** | Remember what's been said before, who maintainers are |
| **OSS-specific safety** | Never force-push upstream, never bulk-act, never forge CLA |

## Setup

### 1. Enable GitHub Actions

In your repository settings, make sure GitHub Actions is enabled:
https://github.com/Mr-Neutr0n/oss-tracker/settings/actions

### 2. Verify `GITHUB_TOKEN` Permissions

The workflow uses `GITHUB_TOKEN` which is automatically provided. Make sure it has `contents: write` and `issues: write` permissions (already set in the workflow).

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
- **GitHub Actions**: Ubuntu runner = free (public repos get unlimited minutes)
- **Total**: $0

## Troubleshooting

| Issue | Solution |
|---|---|
| opencode install fails | Check network connectivity; retry workflow |
| Zen model not found | Verify `opencode.json` is correctly written in workflow |
| PR analysis fails | Check `GITHUB_TOKEN` permissions; ensure `repo` scope is granted |
| Batch creates no actions | Queue may be empty or all PRs recently nudged |
| Rate limit exceeded | Zen free tier allows 100 requests/day; batch size is already capped at 5 |

## Future Enhancements

1. **Automated fixes**: Checkout fork, apply patch, push to fork (not upstream)
2. **CI log parser**: Automatically fetch and parse CI failure logs
3. **Local test verification**: Run `pytest` before pushing fix commits
4. **Smart deferral**: Auto-requeue PRs after maintainer replies
5. **Multi-model**: Use `zen/gpt-5-nano` for quick triage, `zen/big-pickle` for deep analysis

## License

Same as the harness. This is Hari's personal automation.
