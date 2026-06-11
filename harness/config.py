"""Configuration: .oss-harness/config.json plus defaults for new sections.

Loaders are tolerant — missing keys fall back to defaults so an old config
never crashes a run (config drift is an edge case like any other).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_DIR = ".oss-harness"


@dataclass(frozen=True)
class Limits:
    daily_new_pr_cap: int = 5
    daily_comment_cap: int = 10
    max_open_per_repo: int = 2
    cooldown_after_close_days: int = 30
    burst_window_max_prs: int = 3
    burst_window_hours: int = 24
    nudge_interval_days: int = 30
    nudge_lifetime_cap: int = 2
    max_fix_iterations: int = 3
    max_files_changed: int = 6
    max_lines_changed: int = 300
    max_model_calls_per_contribution: int = 12
    max_minutes_per_contribution: int = 25
    candidate_ttl_days: int = 21
    heartbeat_alarm_hours: int = 25


@dataclass(frozen=True)
class ModelEndpoint:
    name: str
    base_url: str
    model: str
    api_key_env: str = ""  # empty = anonymous (Zen free tier needs no key)
    timeout_s: int = 240
    max_tokens: int = 8000


@dataclass(frozen=True)
class Config:
    login: str = "Mr-Neutr0n"
    git_email: str = "64578610+Mr-Neutr0n@users.noreply.github.com"
    tracker_repo: str = "Mr-Neutr0n/oss-tracker"
    dco_authorized: bool = True
    disclosure: str = (
        "This change was prepared with AI assistance under human direction and review."
    )
    limits: Limits = field(default_factory=Limits)
    # Anonymous Zen free tier (verified 2026-06-11; no key required).
    # Order set by patch-task bench (parse/apply/tests + latency):
    #   big-pickle 19s pass · north-mini-code 6s pass · deepseek-flash 49s pass
    #   · nemotron 52s pass · mimo FAILED format (dropped).
    # big-pickle stays primary: it is opencode's curated alias, re-pointed when
    # free promotions rotate, so it self-heals. Rotated-away models 401 and the
    # chain advances (DOMAIN_MODEL.md §7).
    endpoints: tuple[ModelEndpoint, ...] = (
        ModelEndpoint("zen-pickle", "https://opencode.ai/zen/v1", "big-pickle"),
        ModelEndpoint("zen-north", "https://opencode.ai/zen/v1", "north-mini-code-free"),
        ModelEndpoint("zen-deepseek", "https://opencode.ai/zen/v1", "deepseek-v4-flash-free"),
        ModelEndpoint("zen-nemotron", "https://opencode.ai/zen/v1", "nemotron-3-ultra-free"),
    )
    secret_patterns_file: str = f"{HARNESS_DIR}/lint/secret-patterns.txt"
    exclude_owners: tuple[str, ...] = ("Mr-Neutr0n", "baby-ai-stealth")

    @property
    def state_dir(self) -> Path:
        return Path(HARNESS_DIR) / "state"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.jsonl"

    @property
    def reports_dir(self) -> Path:
        return Path(HARNESS_DIR) / "reports"


def load(root: Path | str = ".") -> Config:
    root = Path(root)
    raw: dict = {}
    cfg_path = root / HARNESS_DIR / "config.json"
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            raw = {}  # corrupt config → defaults; the run report will say so

    lim_raw = raw.get("limits", {})
    auto_raw = raw.get("autonomy", {})
    limits = Limits(
        daily_new_pr_cap=lim_raw.get("max_daily_fixes", Limits.daily_new_pr_cap),
        daily_comment_cap=min(lim_raw.get("daily_comment_cap", Limits.daily_comment_cap), 10),
        max_open_per_repo=lim_raw.get("max_open_per_repo", Limits.max_open_per_repo),
        cooldown_after_close_days=lim_raw.get("cooldown_after_close_days", Limits.cooldown_after_close_days),
        burst_window_max_prs=lim_raw.get("burst_window_max_prs", Limits.burst_window_max_prs),
        burst_window_hours=lim_raw.get("burst_window_hours", Limits.burst_window_hours),
        nudge_interval_days=auto_raw.get("nudge_interval_days", Limits.nudge_interval_days),
        nudge_lifetime_cap=auto_raw.get("nudge_lifetime_cap", Limits.nudge_lifetime_cap),
        max_fix_iterations=auto_raw.get("max_fix_iterations", Limits.max_fix_iterations),
        max_files_changed=auto_raw.get("max_files_changed", Limits.max_files_changed),
        max_lines_changed=auto_raw.get("max_lines_changed", Limits.max_lines_changed),
    )

    endpoints = tuple(
        ModelEndpoint(
            name=e["name"], base_url=e["base_url"], model=e["model"],
            api_key_env=e.get("api_key_env", ""),
            timeout_s=e.get("timeout_s", 240), max_tokens=e.get("max_tokens", 8000),
        )
        for e in raw.get("models", {}).get("chain", [])
    ) or Config.endpoints

    user = raw.get("user", {})
    return Config(
        login=user.get("login", Config.login),
        dco_authorized=auto_raw.get("dco_authorized", True),
        limits=limits,
        endpoints=endpoints,
        exclude_owners=tuple(raw.get("scope", {}).get("exclude_owners", Config.exclude_owners)),
    )
