"""Local verification: run the target repo's tests with a scrubbed environment.

Rules from DOMAIN_MODEL.md §5:
  - infra_failure ≠ test failure (don't feed unfixable infra to the model)
  - baseline before patch; only the delta is the patch's fault
  - no_tests = pass-with-flag (disclosed in the PR body)
  - target-repo code never sees our credentials (§2 exfiltration rule)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# NB: PAT must be word-bounded — a bare "PAT" substring would strip PATH itself
SECRET_ENV_RE = re.compile(
    r"(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL|(^|_)PAT(_|$))", re.IGNORECASE)
COMMAND_TIMEOUT_S = 600
GLOBAL_BUDGET_S = 20 * 60

INFRA_MARKERS = (
    "command not found", "No such file or directory", "ModuleNotFoundError",
    "No module named", "Cannot find module", "ENOENT", "could not find a version",
    "unable to resolve dependency", "permission denied", "docker: not found",
    "gpg: ", "error: linker", "SSL certificate",
)


def scrubbed_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV_RE.search(k)}
    env.setdefault("CI", "true")
    return env


@dataclass(frozen=True)
class VerifyResult:
    outcome: str  # passed | failed | no_tests | infra_failure | timeout
    detail: str = ""
    failing: frozenset = frozenset()  # failing test identifiers, when parseable


def detect_commands(repo_path: Path) -> list[list[str]]:
    """Prefer repo-declared commands over heuristics."""
    p = repo_path
    if (p / "package.json").exists():
        try:
            scripts = json.loads((p / "package.json").read_text()).get("scripts", {})
            if "test" in scripts and "no test specified" not in scripts["test"]:
                runner = "bun" if (p / "bun.lockb").exists() or (p / "bun.lock").exists() \
                    else "yarn" if (p / "yarn.lock").exists() \
                    else "pnpm" if (p / "pnpm-lock.yaml").exists() else "npm"
                return [[runner, "test"] if runner != "npm" else ["npm", "test", "--silent"]]
        except (json.JSONDecodeError, OSError):
            pass
    if any((p / f).exists() for f in ("pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini")) \
            or (p / "tests").is_dir() or (p / "test").is_dir():
        if (p / "go.mod").exists() or (p / "Cargo.toml").exists():
            pass  # fall through to compiled-language runners below
        else:
            return [["python3", "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider"]]
    if (p / "go.mod").exists():
        return [["go", "test", "./..."]]
    if (p / "Cargo.toml").exists():
        return [["cargo", "test", "--quiet"]]
    if (p / "Gemfile").exists() and (p / "spec").is_dir():
        return [["bundle", "exec", "rspec"]]
    if (p / "Makefile").exists() and re.search(r"^test:", (p / "Makefile").read_text(errors="replace"), re.M):
        return [["make", "test"]]
    return []


def run_tests(repo_path: Path, commands: list[list[str]] | None = None,
              runner=subprocess.run) -> VerifyResult:
    commands = commands if commands is not None else detect_commands(repo_path)
    if not commands:
        return VerifyResult("no_tests")
    spent = 0.0
    for cmd in commands:
        budget = min(COMMAND_TIMEOUT_S, GLOBAL_BUDGET_S - spent)
        if budget <= 0:
            return VerifyResult("timeout", "global verification budget exhausted")
        try:
            r = runner(cmd, cwd=repo_path, capture_output=True, text=True,
                       timeout=budget, env=scrubbed_env())
        except subprocess.TimeoutExpired:
            return VerifyResult("timeout", f"{' '.join(cmd)} exceeded {budget:.0f}s")
        except FileNotFoundError:
            return VerifyResult("infra_failure", f"runner missing: {cmd[0]}")
        if r.returncode != 0:
            tail = ((r.stdout or "") + "\n" + (r.stderr or ""))[-4000:]
            if any(m in tail for m in INFRA_MARKERS):
                return VerifyResult("infra_failure", tail[-1500:])
            return VerifyResult("failed", tail[-1500:], failing=_failing_ids(tail))
    return VerifyResult("passed")


def _failing_ids(output: str) -> frozenset:
    """Best-effort failing-test identifiers (pytest style) for baseline deltas."""
    return frozenset(re.findall(r"FAILED ([\w/\.:\[\]-]+)", output))


def delta(baseline: VerifyResult, after: VerifyResult) -> VerifyResult:
    """Blame the patch only for NEW failures (§5 baseline rule)."""
    if after.outcome != "failed":
        return after
    if baseline.outcome == "failed":
        new = after.failing - baseline.failing
        if after.failing and not new:
            return VerifyResult("passed", "only pre-existing failures remain", after.failing)
        if new:
            return VerifyResult("failed", f"new failures: {', '.join(sorted(new)[:5])}", frozenset(new))
        # unparseable identifiers on both sides: conservatively keep the failure
    return after
