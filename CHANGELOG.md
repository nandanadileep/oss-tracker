# Changelog

All notable changes to this harness are documented here. Format: semver-ish, date-stamped.

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

Initial scaffold. Bare-minimum harness for managing Hari's (@Mr-Neutr0n) open-source contribution backlog.

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
