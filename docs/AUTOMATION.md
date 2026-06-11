# Automation

The harness runs four GitHub Actions workflows. Code lives in `harness/`
(see `docs/DOMAIN_MODEL.md` for the full design). The old `agent/*.py`
scripts were replaced in 0.3.0; 0.4.0 added the sandbox patch engine.

## How a fix gets made (patch engines)

**Sandbox (primary).** The contribute workflow installs the opencode CLI and
runs it *agentically* inside the cloned fork — the same architecture as
running Claude Code in a local checkout: the agent reads the real files,
edits in place, runs tests, iterates. It runs with a scrubbed environment
(no PAT, no keys), an isolated config (`OPENCODE_CONFIG`, anonymous Zen),
a 15-minute cap, and zero GitHub access. Its `git diff` then passes the same
deterministic gates as everything else (`patch.validate_worktree`: forbidden
paths, deletion/truncation caps, secret scan, size caps) before the harness
commits, pushes to the fork, and opens the PR.

**One-shot (fallback).** If the CLI is missing or the session fails, the
old pipeline runs: context pack → strict PATH/SEARCH-REPLACE contract →
parse → fuzzy apply → same gates. Select explicitly with the `engine`
workflow input or `--engine sandbox|oneshot|auto`.

## Workflows

| Workflow | Cron (UTC) | What it does |
|---|---|---|
| **Contribute** (`oss-contribute.yml`) | `41 4 * * *` daily | Takes queued candidates → live claim-check → preflight → fork → patch (model) → verify → PR. Caps at 5 PRs/day. |
| **Steward** (`oss-steward.yml`) | `17 7 * * *` daily | Reviews existing open PRs: withdraw on stop-signals, close in archived repos, escalate CLA / maintainer replies, nudge within NudgePolicy. |
| **Discover** (`oss-discover.yml`) | `23 2 * * 1` weekly | Searches good-first-issue / help-wanted feeds, screens by signals, queues A/B-tier candidates. |
| **Heartbeat** (`oss-heartbeat.yml`) | `53 */6 * * *` | Watchdog: no successful run in >25h → opens a `needs-human` issue. Outside the shared concurrency group on purpose. |

Crons are on odd minutes (the `:00`/`:30` slots get delayed or dropped), and
the three state-writing workflows share `concurrency.group: oss-harness` so
runs queue instead of racing pushes to main.

## Secrets

| Secret | Used for |
|---|---|
| `GH_PAT` | Classic PAT (`repo` + `workflow`): all cross-repo operations and state pushes. `GITHUB_TOKEN` cannot write cross-repo. **Required.** |
| `OPENCODE_API_KEY` | **Optional.** The default model chain uses the Zen free tier *anonymously* (no key — verified live 2026-06-11). Set this only if you add paid endpoints to `config.json:models.chain` as fallbacks. |

The model chain (`harness/model.py`) calls the Zen gateway over plain HTTPS
with a hard wall-clock deadline per call (socket timeouts alone don't fire
when a server drips keepalives — observed in CI). Free models rotate without
notice; when one 401s ("Free promotion has ended") the chain advances to the
next. `big-pickle` is opencode's curated alias that they re-point to a
current free model, so it self-heals across rotations. Chain order is set by
a patch-task bench: big-pickle → north-mini-code-free → deepseek-v4-flash-free
→ nemotron-3-ultra-free.

Contribute and Steward begin with a **gateway probe** step — two timed curl
calls to the Zen gateway — so every run's log states the runner→gateway
latency up front. CI runners are measurably slower to the gateway than
residential connections (~100s vs ~5-20s per completion observed); the probe
makes that visible instead of mysterious.

## State

Everything is a fold over `.oss-harness/state/ledger.jsonl` (append-only,
union-merged via `.gitattributes`, committed back `if: always()` by
`scripts/commit-state.sh`). `queue.json` is a rebuilt view — never hand-edit.

## Human touchpoints

The only Hari-facing surface is GitHub issues labeled `needs-human` on this
repo: CLA signatures, maintainer replies needing judgment, new-dependency
approvals, harness degradation. Reply with `RESOLVE: <option>`; the blocking
scope (org / repo / contribution / global) is skipped until resolved.

## Running locally

```bash
pytest tests/ -q                                   # 89 tests
python3 -m harness.apps.discover  --dry-run        # search + screen, no writes
python3 -m harness.apps.steward   --dry-run --batch-size 5
python3 -m harness.apps.contribute --dry-run
python3 -m harness.apps.heartbeat --dry-run
```

`--dry-run` journals every decision to the ledger but executes nothing —
the executor records what it *would* have done.
