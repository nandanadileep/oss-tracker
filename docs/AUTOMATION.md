# Automation

The harness runs four GitHub Actions workflows. Code lives in `harness/`
(see `docs/DOMAIN_MODEL.md` for the full design). The old `agent/*.py`
scripts and opencode-CLI transport were replaced in 0.3.0.

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

The model chain (`harness/model.py`) calls the Zen gateway over plain HTTPS —
no opencode CLI. Free models rotate without notice; when one 401s ("Free
promotion has ended") the chain advances to the next. `big-pickle` is
opencode's curated alias that they re-point to a current free model, so it
self-heals across rotations.

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
