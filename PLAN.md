# OSS Automation Plan

> How to evolve our local harness into a GitHub Actions-native automation that runs autonomously every day, using `opencode` in headless mode instead of raw API keys.

## 1. Comparison: Nandana's Setup vs Our Harness

### What Nandana's `autonomous-ci-repair` Does Better

| Feature | Her Implementation | Ours (Current) |
|---|---|---|
| **Runtime** | GitHub Actions reusable workflow (`uses: .../self_healing.yml@main`) | Local agent session only |
| **Trigger** | On every push / PR automatically | On-demand when user opens a session |
| **Models** | Dual-model: **Reader** (analysis) + **Coder** (patch generation) | Single agent (me) |
| **Guardrails** | Deterministic state machine: auto-pilot, auto-commit, anti-loop, partial-retry | Policy-based safety rules (`AGENTS.md`) |
| **Patching** | Hyper-fuzzy patching (80% similarity via `difflib`) — handles LLM context hallucination | Manual edits with exact matching |
| **CI Integration** | Parses logs, generates fix, verifies with tests, commits `[ci-auto-fix]`, retriggers | Diagnoses CI, sometimes pushes fixes, but no automated verification loop |
| **State Tracking** | `agent/state.py` — explicit attempt counter, file edit log, termination conditions | `progress.md` — narrative log |
| **Patch Validation** | Detects additive patches, auto-converts to replacement patches | No automated patch validation |
| **Infinite Loop Protection** | Anti-loop forcing: after 2 consecutive reads, force patch generation | No automated loop detection |
| **Partial Success** | Detects when error count decreases but >0, forces iterative retry | Abandons after one attempt |

### What Our Harness Does Better

| Feature | Our Implementation | Hers |
|---|---|---|
| **OSS Safety** | Hard rules: never force-push upstream, never bulk-act, one PR at a time | No OSS-specific rules; commits directly to the repo it's running in |
| **Queue System** | Sequential queue with tier ratings (A/B/C/F), preflight checks | No queue; reacts only to CI failures on the current repo |
| **Rich Context** | `profiles/`, `docs/quality-bar.md`, `progress.md`, `PR_REPORT.md` — narrative context | Minimal context; focuses only on the failing test file |
| **External PRs** | Handles 220+ PRs across 233 external repos | Only works on the repo where the workflow is installed |
| **Secret Scanning** | `lint/secret-blocklist.py` — scans every new file for token patterns | No secret scanning |
| **Burst Detection** | `lint/burst-detect.py` — flags mass-PR patterns | No burst detection |
| **Title Uniformity** | `lint/title-uniformity.py` — flags template-driven PR titles | No title checking |
| **Repo Preflight** | `scanners/repo-preflight.py` — checks archived, license, CoC before engaging | No preflight |
| **No API Keys** | Uses `gh` CLI only, no LLM API keys needed for most operations | Requires `GEMINI_API_KEY` and `GROQ_API_KEY` secrets |
| **Cost Control** | Agent runs only when user is active; no background costs | Runs on every CI failure, potentially many LLM calls per day |

### Key Insight

Nandana's tool is a **repo-local CI repair agent** — it fixes the repo it's installed in. Our harness is a **cross-repo OSS contribution manager** — it tracks and advances PRs across hundreds of external repos. These are different problems, but her guardrails and automation patterns are exactly what we need to add to our harness.

## 2. Proposed Architecture

### Runner Strategy: GitHub Actions Cloud

The automation runs on GitHub-hosted `ubuntu-latest` runners, installing `opencode` fresh each time:

- **Runs 24/7** — No dependency on your laptop being on
- **Truly free** — OpenCode Zen free tier + GitHub Actions public repo minutes = $0
- **No API keys** — Zen free tier requires no authentication
- **GitHub-native** — Uses `GITHUB_TOKEN` for all operations
- **Visible** — Workflow runs show in GitHub Actions UI

### Installation in Workflow

```bash
# Install opencode via official script
curl -fsSL https://opencode.ai/install | bash

# Configure Zen provider (no API key needed)
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json << 'EOF'
{
  "model": "zen/big-pickle",
  "provider": {
    "zen": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://opencode.ai/zen/v1"
      }
    }
  }
}
EOF
```

### Automation Flow

```
Daily Cron (GitHub Actions, self-hosted runner)
  ↓
Checkout oss-tracker repo
  ↓
Read .oss-harness/state/queue.json
  ↓
Select top 5 PRs (A-tier first, then B, then B-)
  ↓
For each PR:
  a. Live fetch: gh pr view, comments, CI status, review threads
  b. Run opencode in headless mode with structured prompt
  c. Parse JSON decision output (nudge / fix / close / skip)
  d. Execute decision via gh CLI
  e. Log action to progress.md
  ↓
Update .oss-harness/state/queue.json (advance, mark done, defer)
  ↓
Commit and push updates to oss-tracker
```

### `opencode` Headless Mode

Based on the docs and the user's existing setup:

```bash
# Option 1: Direct run (creates a new session each time)
opencode run --format json --model zen/big-pickle \
  "Analyze PR #123 in repo X. Context: [structured data]. Return JSON with decision and reasoning."

# Option 2: Attach to a running server (faster, no cold boot)
# Start server once: opencode serve
# Then attach:
opencode run --attach http://localhost:4096 --format json \
  "Analyze PR #123..."
```

For the automation, we use direct run (`opencode run`) because each workflow is ephemeral — no need to maintain a persistent server.

## 3. Safety Codified in the Automation

We take our `AGENTS.md` hard rules and turn them into code:

| Rule | Implementation |
|---|---|
| Never bulk-act | Batch size hard-coded to 5 PRs max per run |
| Never force-push upstream | `gh` CLI operations are read-only or comment-only; code pushes only to `Mr-Neutr0n/*` forks |
| Comments after reading | `opencode` prompt includes the full PR context (comments, reviews, CI) before deciding |
| One PR at a time | Sequential loop within the batch; no parallel PR execution |
| Never re-open/un-close | Close action requires a specific `reason` field in the JSON output; blocked without it |
| Daily cap | Workflow stops after 5 PRs regardless of queue size |
| Secret scan | `lint/secret-blocklist.py` runs on any new file before commit |
| Human gate for destructive actions | Close actions create a GitHub Issue in oss-tracker for human approval instead of executing immediately |

## 4. Implementation Plan

### Phase 1: Infrastructure (This Session)

1. Create `.github/workflows/daily-oss-agent.yml` — the main cron workflow
   - Runs on `ubuntu-latest` (GitHub-hosted)
   - Installs opencode via official install script
   - Configures Zen provider with no API key
   - Runs the daily batch
2. Create `agent/opencode-runner.py` — Python wrapper that:
   - Runs `opencode run` for each PR
   - Parses JSON output
   - Executes decisions via `gh` CLI
3. Create `scripts/daily-batch.sh` — shell orchestrator

### Phase 2: Guardrails (Next Session)

1. Port Nandana's guardrails to our agent:
   - `guardrails.py`: Auto-pilot (apply patch immediately), Anti-loop (max 2 reads), Partial-retry
2. Port hyper-fuzzy patching for automated fixes
3. Add attempt tracking (max 3 attempts per PR per session)

### Phase 3: Intelligence (Future)

1. Dual-model setup: use `zen/big-pickle` for analysis, `zen/gpt-5-nano` for quick triage
2. CI log parser: automatically fetch and parse CI failure logs
3. Automated test verification: run `pytest` locally before pushing fix commits

## 5. Files to Create

| File | Purpose |
|---|---|
| `.github/workflows/daily-oss-agent.yml` | Main cron workflow (GitHub-hosted runner) |
| `agent/opencode-runner.py` | Python wrapper for `opencode` CLI |
| `agent/guardrails.py` | Deterministic safety guardrails |
| `agent/state.py` | Attempt tracking, file edit log |
| `scripts/daily-batch.sh` | Shell orchestrator |
| `docs/AUTOMATION.md` | How the automation works |

## 6. Daily Schedule

```yaml
cron: "0 9 * * *"  # 9 AM UTC daily
```

This is morning in US timezones, afternoon in Europe, evening in Asia — a good time for OSS maintainers to be active.

## 7. Expected Behavior

**Day 1:** Workflow runs, analyzes top 5 A-tier PRs, posts 3 nudges, skips 2 (already nudged recently), updates tracker.

**Day 2:** Workflow runs, finds 2 PRs from Day 1 got maintainer replies, analyzes those, generates reply or fix, pushes if fixable.

**Day 3:** Workflow runs, finds 1 PR was merged, marks it done, moves to next in queue.

**Week 1:** ~25 PRs touched. 5-10 get maintainer responses. 2-3 get merged.

---

**Next Step:** Implement Phase 1 (create workflow and runner script).
