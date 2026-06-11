# Changelog

All notable changes to this harness are documented here. Format: semver-ish, date-stamped.

## 0.5.0 — 2026-06-11

Multi-agent sandbox: Cursor CLI as paid fallback behind free opencode.

- **AgentCli chain** in the sandbox engine: opencode (free, anonymous Zen)
  first; Cursor CLI (`agent --model composer-2.5`, `CURSOR_API_KEY`) when the
  free agent is unusable or its session fails — built for runners whose IPs
  can't reach the free gateway. Session failure advances the chain; a gate
  rejection of the diff does not. Identical containment for every agent; the
  only env-scrub exception is the CLI's own key, scoped to its own sessions.
- composer-2.5 pinned explicitly (the `-fast` tier is the same weights at ~6x
  the price, and the CLI has a known default-to-fast bug).
- Live-verified: cursor session fixed the bench bug in 51s, gates passed.
- 109 unit tests.

## 0.4.0 — 2026-06-11

The sandbox patch engine: a real coding agent instead of one-shot patches.

- **Agent-in-sandbox engine** (`harness/sandbox.py`, primary): the opencode CLI
  runs agentically inside the cloned fork — reads real files, edits in place,
  runs tests — like Claude Code in a local checkout. Scrubbed env, isolated
  config, 15-min cap, zero GitHub access; its git diff passes the same gates
  (`patch.validate_worktree`) before commit/push/PR. One-shot pipeline stays
  as fallback (`--engine auto|sandbox|oneshot`).
- **Wall-clock deadline on model calls**: urllib timeouts are socket-inactivity
  only — a CI call sat 12+ min past its "240s timeout" while the server dripped
  keepalives. Every transport call now runs under a hard deadline.
- **Gateway probe step** in Contribute/Steward: timed curls at run start make
  runner→gateway latency visible in every log.
- **Bench-ordered model chain**: big-pickle 19s pass, north-mini-code 6s pass,
  deepseek-flash 49s, nemotron 52s; mimo dropped (failed output contract).
- Stage-by-stage progress streaming; `--only owner/repo#N` targeted test runs;
  dry-runs no longer satisfy the same-day re-run guard.
- 105 unit tests.

## 0.3.0 — 2026-06-11

Full rewrite as the `harness/` package implementing `docs/DOMAIN_MODEL.md`.

- **Decide/act separation**: all external writes go through `ProposedAction` →
  deterministic `ActionValidator` (rate caps, burst window, lint, secret scan,
  relationship standing, idempotency) → `Executor`. The model never holds writes.
- **Event-sourced state**: `.oss-harness/state/ledger.jsonl` is the source of
  truth; queue/relationships/budgets are folds. Legacy queue.json migrated
  (161 queued + 41 done) via `scripts/migrate-legacy-state.py`.
- **Direct-HTTP model chain** (`harness/model.py`) replaces the opencode CLI:
  anonymous Zen free tier (no API key — verified live), 524→125s backoff,
  429 Retry-After, compact-context timeout retry, fallback past rotated-away
  free models, per-contribution budgets. Root cause of every New Contributor failure.
- **Four workflows** replace three: Contribute (daily), Steward (daily),
  Discover (weekly), Heartbeat (6h watchdog). Odd-minute crons, shared
  concurrency group, union-merge state commits `if: always()`.
- **Reputation model**: per-repo standing (virgin→welcomed/cooled/blocked),
  stop-signal detection, NudgePolicy, AI-policy preflight, PR disclosure line.
- **Safety**: env-scrubbed test execution in target repos, fork-only force-push
  invariant, injection heuristics, deletion/truncation guardrails, escalation
  issues (`needs-human`) with blocking scopes — CLA et al. come to Hari, nothing else does.
- 89 unit tests (`pytest tests/`). Old `agent/*.py` scripts removed.

## 0.2.0 — 2026-06-02

Second session. Switched from "nudge only" to "do the work" mode per user request.

What changed in 0.2.0:

- **`config.json:limits.daily_comment_cap`** raised from 5 → 50. Per-repo cap (2) and cooldown (30d) unchanged.
- **14 PRs processed** this session (5 nudges from 0.1.0 + 9 new actions):
  - 5 nudges (already-shipped-friendly): anthropics/skills #361/#362, PaddlePaddle/PaddleOCR#17685, Lightning-AI/pytorch-lightning#21539, Stability-AI/generative-models#470.
  - 6 pushed code fixes: mage-ai/mage-ai#6024 (added 'datetime' to coltype set), chroma-core/chroma#6437 (dropped ineffective __call__ mock), lancedb/lancedb#3028 (file format v2.1), huggingface/diffusers#13093 (use get_device() helper), agent-lightning#481 (3 new tests for session leak fix), agent-lightning#482 (totoluo's is_drop_list suggestion).
  - 7 discussion/clarification comments: cudf#21444 (superseded by upstream #22328), vllm#34163 (bot comments stale, refactor already done), PyGithub#3450 (test intent), agent-lightning#480/#483/#484/#485/#486 (maintainer questions, awaiting choice).
- **Queue** updated: 197 actionable + 10 deferred + 14 done.
- **Local test capability** for Python-only projects (chroma, lancedb, mage-ai, agent-lightning, diffusers) via `uv venv + uv pip install`. CUDA-only projects (cudf) cannot be tested locally — noted in queue and PR comments.

## 0.1.0 — 2026-06-01

Initial scaffold. Bare-minimum harness for managing open-source contribution backlog.

What ships in 0.1.0:

- `AGENTS.md` — top-level table of contents (Anthropic / harnesskit pattern).
- `README.md` — human overview, one-screen orientation.
- `VERSION`, `CHANGELOG.md` — versioning.
- `.oss-harness/config.json` — harness config (user, scopes, rate limits, hard stops).
- `.oss-harness/docs/` — architecture, contribution workflow, quality bar, profile health, security policy.
- `.oss-harness/lint/` — burst detector, title-uniformity check, secret-pattern blocklist.
- `.oss-harness/scanners/` — repo security pre-flight (secret scan, archived-repo detector, code-of-conduct awareness).
- `.oss-harness/state/queue.json` — sequential work queue for the PR backlog (one PR at a time, by design).
- `.oss-harness/state/progress.md` — running log of what the harness did in each session (Anthropic "claude-progress.txt" pattern).
- `.oss-harness/reports/PR_REPORT.md` — the 12-section snapshot of the current backlog.
- `.oss-harness/data/` — raw GitHub API responses (search + GraphQL enrich) and the rated PR list.

What's NOT in 0.1.0 (deferred):

- Multi-agent generator/evaluator split. This is a single-session harness; the user explicitly wants PR work done sequentially, not scripted.
- Live re-fetching. Reports are point-in-time snapshots.
- Webhook / GitHub Action integration. This harness runs locally and is invoked manually.
