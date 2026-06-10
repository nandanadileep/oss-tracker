# OSS Autonomy Plan

> Shared domain model for the OSS automation system.

## Intent

This harness should behave like Hari doing daily OSS maintenance, but faster and more consistent.

The system is autonomous. It should read the repo and PR context, act, log what happened, and leave Hari a clean daily review trail. Hari stays in the loop by reviewing outcomes daily, not by approving every normal action before it happens.

## Workflows

### 1. Backlog Steward

Purpose: own existing PRs until they merge or require a human-only action.

Current workflow: `.github/workflows/daily-oss-agent.yml`

Default schedule: 9 AM UTC.

Responsibilities:

- Read existing PR queue.
- Check PR state, comments, reviews, mergeability, and CI.
- Nudge stale green PRs.
- Nudge only when Hari's comment is the latest comment and it is at least 7 days old.
- Reply directly when a maintainer or reviewer has responded after Hari's last comment.
- Do not close PRs for now. Keep nudging, defer, or raise a local issue.
- Fix maintainer-requested issues on Hari's fork branches.
- Raise local `oss-tracker` issues for CLA/DCO/signature blockers that Hari must handle.
- Re-run CI when old logs are gone and a no-op commit is appropriate.
- Record every action in `progress.md`.
- Update structured state so the same PR is not acted on repeatedly.

### 2. New Contributor

Purpose: create new high-quality OSS contributions at a steady daily pace.

Planned workflow: `.github/workflows/daily-new-contributions.yml`

Target schedule: 11 AM IST, which is 05:30 UTC.

Responsibilities:

- Select candidate repos and issues from local `.oss-harness/state/candidates.json` by score, not random order.
- Preflight each repo before engaging.
- Prefer meaningful issues and bigger PRs when confidence is high enough.
- Fork or reuse Hari's fork.
- Create one branch per issue.
- Generate a minimal patch with `opencode`.
- Run local checks when detectable.
- Push to Hari's fork.
- Open a PR to upstream.
- Add the opened PR to Backlog Steward's tracking queue.
- Stop after 1-5 successful high-confidence PRs per day. Do not force low-quality PRs just to hit 5.
- Comment or claim the source issue when appropriate, then open the PR when ready. Keep both messages professional, specific, and non-AI-like.

### 3. Candidate Discovery

Purpose: keep `candidates.json` fresh without manual updates.

Planned workflow: `.github/workflows/discover-candidates.yml`, or a discovery phase inside `.github/workflows/daily-new-contributions.yml`.

Recommended schedule: before New Contributor, or weekly if daily discovery is too noisy.

Responsibilities:

- Find fresh high-activity repositories.
- Add the top 10 repos of the week after deduplication and preflight.
- Search those repos for candidate issues.
- Score repos and issues.
- Append only useful candidates to `.oss-harness/state/candidates.json`.
- Never open PRs directly. Discovery feeds New Contributor.

Discovery sources:

- GitHub Search API for repositories: stars, pushed date, language, recent activity.
- GitHub Search API for issues: labels, comments, age, activity.
- Optional web search for weekly trending GitHub repos, then verify through GitHub API.

EXA is optional. The first version should prefer GitHub API because it is structured, dedupable, and less likely to hallucinate repo metadata.

## Core Entities

### Repository

An upstream GitHub repository.

Fields:

- `name_with_owner`
- `default_branch`
- `archived`
- `disabled`
- `license`
- `stars`
- `last_pushed_at`
- `contributing_url`
- `ai_policy_status`
- `coc_status`
- `maintainer_responsiveness_score`
- `max_open_prs_allowed`: 30
- `discovered_at`
- `discovery_source`: `manual`, `github_search`, `web_search`, `trending_weekly`
- `repo_score`
- `candidate_status`: `new`, `preflighted`, `eligible`, `blocked`, `exhausted`

### Discovery Source

A source that can add repositories or issues into the candidate pool.

Fields:

- `type`: `manual`, `github_search`, `web_search`, `trending_weekly`
- `query`
- `schedule`
- `last_run_at`
- `repos_found`
- `repos_added`
- `issues_added`
- `deduped_count`
- `blocked_count`

### Repository Candidate

A repository that may contain useful contribution targets.

Fields:

- `repo`
- `source`
- `stars`
- `language`
- `pushed_at`
- `open_issues_count`
- `license`
- `archived`
- `ai_policy_status`
- `existing_open_prs_by_hari`
- `repo_score`
- `status`: `new`, `eligible`, `blocked`, `exhausted`

### Pull Request

An existing contribution authored by Hari.

Fields:

- `key`: `owner/repo#number`
- `title`
- `state`
- `tier`
- `head_owner`
- `head_branch`
- `base_branch`
- `mergeable`
- `ci_summary`
- `required_checks_status`
- `last_hari_comment_at`
- `last_maintainer_comment_at`
- `latest_comment_author`
- `should_nudge`: true only when latest comment is Hari's and older than 7 days
- `should_reply`: true when a maintainer/reviewer replied after Hari's last comment
- `last_action_at`
- `next_review_at`
- `status`: `ready`, `cooldown`, `deferred`, `needs_attention`, `cla_blocked`, `done`

### Candidate Issue

A possible new contribution target.

Fields:

- `repo`
- `issue_number`
- `title`
- `labels`
- `body_summary`
- `confidence`
- `repo_score`
- `issue_score`
- `total_score`
- `estimated_scope`: `tiny`, `small`, `medium`, `large`
- `requires_domain_knowledge`
- `has_reproduction`
- `has_tests_hint`
- `maintainer_activity_score`
- `dedupe_key`: `owner/repo#issue_number`
- `selected_at`
- `status`: `new`, `reviewed`, `attempted`, `contributed`, `skipped`, `failed`
- `daily_target_mode`: `adaptive_1_to_5`
- `issue_comment_policy`: `comment_or_claim_then_open_pr`

### Action

One thing the automation did.

Fields:

- `type`: `nudge`, `fix`, `open_pr`, `skip`, `defer`, `rerun_ci`, `create_local_issue`
- `target`
- `reason`
- `confidence`
- `comment_id`
- `commit_sha`
- `pr_url`
- `created_at`
- `workflow_run_id`

## State Files

Recommended state layout:

```text
.oss-harness/state/
  queue.json
  progress.md
  actions.jsonl
  repo-memory.json
  candidates.json
  discovery.json
  new-prs.jsonl
```

`progress.md` stays human-readable. `actions.jsonl` becomes the machine-readable source of truth.

`candidates.json` is not meant to be hand-maintained forever. It is the scored working queue produced by manual additions plus Candidate Discovery.

## Autonomy Rules

Allowed without pre-approval:

- Comment on a PR after reading live context.
- Push fixes to Hari-owned fork branches.
- Open new PRs from Hari-owned fork branches.
- Comment or claim source issues before opening PRs when appropriate.
- Add fresh repositories and issues to the candidate queue after preflight and dedupe.
- Re-run CI by pushing a no-op commit to Hari-owned fork branches when old logs are unavailable.
- Create an `oss-tracker` issue for CLA/DCO/signature blockers.

Never allowed:

- Force-push to an upstream-owned branch.
- Forge DCO, CLA, or maintainer approval.
- Comment on archived repos.
- Close PRs automatically for now.
- Reopen or unclose a PR without explicit human instruction.
- Open PRs against repos that ban AI-assisted contributions.
- Create bursty, repetitive, low-quality PRs.
- Post generic bumps when a maintainer reply needs a real answer.
- Open PRs directly from discovery without candidate scoring and verification.

## Borrowed Patterns From Nandana's Repo

Useful:

- Process candidate batches until at least one contribution is created.
- Track candidate status as contributed, reviewed, skipped, or failed.
- Detect existing PRs before opening duplicates.
- Use patch-only edits for large files.
- Reject suspicious mass deletions.
- Probe common test commands before pushing.

Needs changes:

- Replace OpenAI-compatible API-key client with `opencode run` and Zen models.
- Replace Notion with `.oss-harness/state` files.
- Replace `git add -A` with explicit modified-file staging.
- Add OSS reputation controls before candidate selection.
- Add PR follow-up handoff after opening a new PR.

## Outside Patterns Worth Copying

From current OSS automation examples:

- Use separate roles for finder, fixer, reviewer, and follow-up instead of one giant prompt.
- Deduplicate active runs so two jobs never fix the same PR at once.
- Only touch agent-owned branches.
- Keep a max-open-PRs-per-repo limit.
- Stop when GitHub API rate limit drops below a safety floor.
- Run a second model or second pass review before opening new PRs when the change is non-trivial.

## Build Order

### Phase 1: Make Backlog Steward Real

- Status: implemented for the existing daily workflow.
- State engine rotates queue items, sets cooldowns, and marks `needs_attention`.
- Repo preflight runs before comment actions.
- Structured `actions.jsonl` logging is written for each action.
- Comment lint runs before posting.
- PR context includes comments, maintainer reply detection, files, check names, and CI summary.
- CLA/DCO blockers create local issues.
- Nudge/reply policy is deterministic: 7-day threshold if latest comment is Hari's, direct reply if maintainer replied.
- Close is blocked in code.

### Phase 2: Add Maintainer Fix Loop

- Status: first implementation added to Backlog Steward.
- Detect maintainer-requested changes and red CI as `fix` actions.
- Checkout only Hari-owned fork branches.
- Ask `opencode` for a minimal search/replace patch.
- Apply patch with exact-match and deletion guardrails.
- Run opencode-proposed or detected local tests where possible.
- Stage only files touched by the patch.
- Push to fork without force.
- Ask `opencode` to draft the follow-up comment.
- Lint the follow-up comment before posting.

Public text ownership rule:

- Deterministic code may decide action, block unsafe paths, and validate text.
- `opencode` must draft upstream PR comments, maintainer replies, local issue bodies, fix follow-up comments, and commit messages.
- If opencode cannot produce lint-clean text after retry, the PR moves to `needs_attention` instead of posting a hardcoded fallback.

### Phase 3: Add New Contributor Workflow

- Status: implemented.
- Build candidate scoring and candidate consumption.
- Preflight repos.
- Support larger issues, but require explicit plan, confidence score, and local verification before opening PRs.
- For now, skip second-pass AI review. Revisit later if the workflow becomes more agentic.
- Push branches and open PRs.
- Add opened PRs to Backlog Steward.

### Phase 3.5: Add Candidate Discovery

- Status: implemented.
- Query GitHub for high-activity repos.
- Optionally use web search for weekly trending repos.
- Add top 10 weekly repos after dedupe and preflight.
- Search candidate issues in eligible repos.
- Score issues and write to `candidates.json`.

### Phase 4: Repo Memory

- Track which repos merge Hari's work.
- Track maintainers who ask for changes vs ignore PRs.
- Lower priority for repos with no merge activity.
- Raise priority for repos where small fixes get merged.

## Open Decisions

Resolved decisions:

1. Candidate source: local `.oss-harness/state/candidates.json`.
2. Daily new contribution target: adaptive 1-5 high-confidence PRs.
3. New PR scope: allow bigger issues and PRs, not only tiny fixes.
4. Close policy: never close PRs for now, keep nudging or defer.
5. Fix policy: safe to fix queued PRs because they are Hari-created PRs. For CLA blockers, create an `oss-tracker` issue and wait for Hari.
6. Max open PRs per upstream repo: 30.
7. Larger new PRs do not require second-pass AI review for now.
8. Source issues: do both where appropriate, comment or claim first, then open PR when ready.
9. Nudge rule: if latest comment is Hari's and older than 7 days, nudge. If someone replied after Hari, reply to them.
10. Candidate queue should not be manual-only. Add Candidate Discovery from GitHub trending/search, deduped and scored.

Still open:

1. What exact labels should the New Contributor workflow prefer beyond `good first issue`, `help wanted`, and `bug`?
2. Should issue claiming wait before implementation, or can the agent comment and immediately start work in the same run?
3. Should max 30 open PRs per repo include closed-but-unmerged cooldown windows, or only currently open PRs?
4. Should Candidate Discovery run daily or weekly?

## Review Report

### CEO Review

Verdict: **promising, but the product risk is reputation, not automation throughput**.

The plan correctly separates existing-PR stewardship from new contribution creation. That is the right product boundary. Backlog Steward improves existing relationships. New Contributor creates new social surface area. Those two jobs need different safety rails.

Main CEO findings:

- Backlog Steward is the strongest wedge. It solves an existing painful problem: PRs rot unless someone reads replies, fixes CI, and follows up. Ship this first.
- New Contributor should not optimize for daily PR count. The plan says adaptive 1-5, which is right. The real score is accepted PRs and positive maintainer replies, not PRs opened.
- Candidate Discovery should start weekly, not daily. Daily discovery will create noise before the scoring model has real feedback data.
- Top 10 trending repos is a useful input, but not a strategy by itself. Trending repos often have high maintainer load and noisy issue trackers. Score responsiveness and contribution friendliness above raw stars.
- Bigger PRs are allowed, but they need a stricter "why this will get merged" check. Big PRs without maintainer context can become abandoned work.
- The system needs a daily review digest. Hari should not have to read raw Actions logs to understand what happened.

CEO recommendation:

1. Ship Backlog Steward state engine first.
2. Add Candidate Discovery as weekly scored intake.
3. Gate New Contributor on candidate score, repo responsiveness, and local verification.
4. Measure success by merge rate, maintainer replies, and no negative maintainer signals.

### CTO Review

Verdict: **architecture is workable, but current implementation is still a shell. Do not add New Contributor before state and guardrails are real**.

The current code path still has several mismatches with the plan:

- `queue.json` is not advanced.
- `actions.jsonl` does not exist.
- `stale_days` is hardcoded to `0`.
- Comments are not sorted or classified into Hari vs maintainer replies.
- The prompt still includes `close` even though close is disabled by policy.
- `daily-batch.sh` can still execute `close` if the model returns it.
- `repo-preflight.py` exists but is not wired into the batch path.
- `requires_human` is parsed but unused in the batch path. That is acceptable as policy, but then CLA/DCO detection must become deterministic, not model-vibes.
- The Python runner and shell script duplicate decision execution. This will drift.
- Failure reporting claims issues may be created, but actual failure issue creation only happens on workflow failure, not per-PR failure.

CTO recommendation:

1. Move orchestration into Python. Keep shell as a thin wrapper.
2. Create a state engine before any new workflow.
3. Add deterministic action gates before model output can execute.
4. Disable close in code, not only docs.
5. Implement comment classifier before nudging.
6. Add preflight as a hard step before any comment, claim, PR, or fork.
7. Add structured logs first, then build dashboards/digests from them.

### Required Gates Before New Contributor

New Contributor should not run autonomously until these exist:

- `actions.jsonl` records every action with target, reason, confidence, and workflow run ID.
- Queue state rotates and prevents repeated nudges.
- Comment classifier distinguishes Hari, maintainer, bot, and reviewer comments.
- 7-day nudge rule is deterministic.
- Close action is blocked in code.
- Repo preflight blocks archived, disabled, and AI-ban repos.
- Public-facing comments are opencode-authored and linted. No hardcoded fallback comments.
- Candidate scoring is deterministic and saved.
- Duplicate issue/PR detection works.
- Local verification result is included in every new PR body.
- Daily digest summarizes actions, skipped items, failures, and local issues created.

### Suggested State Machine

```text
Candidate Discovery
  -> repo found
      -> preflight
          -> blocked -> discovery.blocked
          -> eligible
              -> issue search
                  -> score issue
                      -> candidates.json:new

New Contributor
  -> candidates.json:new sorted by total_score
      -> claim/comment issue
          -> clone/fork
              -> patch
                  -> local verify
                      -> failed -> candidates.json:failed
                      -> passed -> open PR
                          -> queue.json:ready

Backlog Steward
  -> queue.json:ready
      -> classify latest activity
          -> latest is Hari and age >= 7d -> nudge
          -> maintainer replied -> reply/fix
          -> CLA/DCO blocked -> create local issue
          -> CI needs fix -> fix loop
          -> otherwise -> cooldown/defer
```

### Test Plan Required By CTO Review

Minimum tests before enabling autonomous writes:

- Queue rotation: nudged PR moves to cooldown with `next_review_at`.
- Comment classifier: Hari latest vs maintainer latest vs bot latest.
- Nudge rule: exactly 6 days does not nudge, 7 days does.
- Close policy: model returns `close`, executor converts to skip/defer.
- CLA blocker: creates local issue and marks PR `cla_blocked`.
- Preflight: archived repo blocks action.
- Candidate dedupe: same `owner/repo#issue` is not added twice.
- Candidate scoring: higher-score candidate is selected first.
- PR creation: opened PR is added to Backlog Steward queue.
- Dry run: no comments, no pushes, no PRs, but logs planned actions.

### Final Review Call

Approved direction, with one sequencing change:

Do not build the three workflows in parallel. Build in this order:

1. Backlog Steward state engine and deterministic gates.
2. Maintainer fix loop for existing queued PRs.
3. Weekly Candidate Discovery.
4. New Contributor in dry-run mode.
5. New Contributor writes enabled after one clean dry-run week.
