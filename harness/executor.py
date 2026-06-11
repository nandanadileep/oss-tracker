"""The Executor — the ONLY component that writes to the outside world.

Pipeline per action (DOMAIN_MODEL.md §8):
    journal action_proposed → ActionValidator gates → execute via gh (paced)
    → journal action_executed / action_rejected with the receipt.

dry_run replaces the gh layer with a recorder, so apps sanity-check end-to-end
without touching GitHub.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import gh as github
from .config import Config
from .domain import Escalation
from .events import Ev, Ledger
from .ids import new_id
from .policy import ActionValidator, ProposedAction


@dataclass(frozen=True)
class Receipt:
    action_id: str
    outcome: str  # ok | rejected | api_error | dry_run
    detail: str = ""
    url: str = ""


@dataclass
class Executor:
    cfg: Config
    ledger: Ledger
    validator: ActionValidator
    dry_run: bool = False
    executed: list[ProposedAction] = field(default_factory=list)

    def propose(self, kind: str, repo: str, number: int = 0,
                rationale: str = "", **payload) -> ProposedAction:
        return ProposedAction(id=new_id("act"), kind=kind, repo=repo,
                              number=number, payload=payload, rationale=rationale)

    def execute(self, action: ProposedAction) -> Receipt:
        self.ledger.append(Ev.ACTION_PROPOSED, action.subject, action_id=action.id,
                           kind=action.kind, rationale=action.rationale[:300])
        rejections = self.validator.validate(action)
        if rejections:
            detail = "; ".join(f"{r.code}({r.detail})" for r in rejections)
            self.ledger.append(Ev.ACTION_REJECTED, action.subject,
                               action_id=action.id, kind=action.kind, codes=detail)
            return Receipt(action.id, "rejected", detail)

        if self.dry_run:
            self.executed.append(action)
            self.ledger.append(Ev.ACTION_EXECUTED, action.subject, action_id=action.id,
                               kind=action.kind, dry_run=True)
            return Receipt(action.id, "dry_run")

        try:
            url = github.paced_write(self._dispatch, action)
        except github.GhError as e:
            self.ledger.append(Ev.ACTION_REJECTED, action.subject, action_id=action.id,
                               kind=action.kind, codes=f"API_ERROR({str(e)[:200]})")
            return Receipt(action.id, "api_error", str(e)[:200])
        self.executed.append(action)
        self.ledger.append(Ev.ACTION_EXECUTED, action.subject, action_id=action.id,
                           kind=action.kind, url=url or "")
        return Receipt(action.id, "ok", url=url or "")

    # ── kind handlers ──────────────────────────────────────────────────────

    def _dispatch(self, a: ProposedAction) -> str:
        handler = getattr(self, f"_do_{a.kind}", None)
        if handler is None:
            raise github.GhError(f"no handler for kind {a.kind}")
        return handler(a)

    def _do_open_pr(self, a: ProposedAction) -> str:
        p = a.payload
        out = github.gh([
            "pr", "create", "-R", a.repo,
            "--head", p["head"], "--base", p["base"],
            "--title", p["title"], "--body", p["body"],
        ] + (["--draft"] if p.get("draft") else []), timeout=180)
        return out.strip().splitlines()[-1] if out.strip() else ""

    def _do_post_comment(self, a: ProposedAction) -> str:
        github.gh(["pr" if a.payload.get("on", "pr") == "pr" else "issue", "comment",
                   str(a.number), "-R", a.repo, "--body", a.payload["body"]], timeout=120)
        return f"https://github.com/{a.repo}/issues/{a.number}"

    def _do_close_pr(self, a: ProposedAction) -> str:
        args = ["pr", "close", str(a.number), "-R", a.repo]
        if a.payload.get("comment"):
            args += ["--comment", a.payload["comment"]]
        github.gh(args, timeout=120)
        return f"https://github.com/{a.repo}/pull/{a.number}"

    _do_withdraw_pr = _do_close_pr

    def _do_update_pr_body(self, a: ProposedAction) -> str:
        github.gh(["pr", "edit", str(a.number), "-R", a.repo,
                   "--body", a.payload["body"]], timeout=120)
        return f"https://github.com/{a.repo}/pull/{a.number}"

    def _do_open_escalation_issue(self, a: ProposedAction) -> str:
        out = github.gh(["issue", "create", "-R", self.cfg.tracker_repo,
                         "--title", a.payload["title"], "--body", a.payload["body"],
                         "--label", "needs-human"], timeout=120)
        return out.strip().splitlines()[-1] if out.strip() else ""

    # ── escalation convenience (one per subject; re-checked against GitHub) ─

    def escalate(self, esc: Escalation) -> Receipt:
        subject_key = f"{esc.reason.value}:{esc.subject}"
        for ev in self.ledger.events():
            if ev.event == Ev.ESCALATION_OPENED and ev.data.get("key") == subject_key:
                return Receipt("dedup", "ok", "escalation already open")
        action = self.propose("open_escalation_issue", self.cfg.tracker_repo,
                              rationale=esc.recommendation,
                              title=esc.title(), body=esc.body())
        receipt = self.execute(action)
        if receipt.outcome in ("ok", "dry_run"):
            self.ledger.append(Ev.ESCALATION_OPENED, esc.subject,
                               key=subject_key, reason=esc.reason.value, url=receipt.url)
        return receipt

    def open_escalation_keys(self) -> set[str]:
        """Subjects blocked by open escalations (resolved ones removed)."""
        keys: set[str] = set()
        for ev in self.ledger.events():
            if ev.event == Ev.ESCALATION_OPENED:
                keys.add(ev.data.get("key", ""))
            elif ev.event == Ev.ESCALATION_RESOLVED:
                keys.discard(ev.data.get("key", ""))
        keys.discard("")
        return keys
