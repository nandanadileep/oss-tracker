"""oss-tracker harness — autonomous OSS contribution engine.

Implements docs/DOMAIN_MODEL.md. Layout mirrors the bounded contexts:

    events.py    ledger (append-only source of truth) + folds
    domain.py    state machines: Candidate, Contribution, StewardCase,
                 RepoRelationship, Escalation
    config.py    hard stops & tunables (.oss-harness/config.json)
    policy.py    preflight, lint, ActionValidator — the deterministic gates
    patch.py     PatchPlan: parse → validate → fuzzy-apply, guardrails
    model.py     ProviderChain: direct-HTTP LLM calls, retry matrix, budgets
    gh.py        GitHub adapter (gh CLI), pacing, fork lifecycle, CI rollup
    verify.py    local test execution, env scrubbing, baseline deltas
    executor.py  the ONLY writer: ProposedAction → validate → execute
    apps/        entrypoints: discover, contribute, steward, heartbeat
"""

__version__ = "0.2.0"
