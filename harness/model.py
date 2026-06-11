"""ProviderChain: direct-HTTP LLM calls with the full retry matrix.

No CLI transport, ever — the opencode subprocess cold-boot is what killed the
old runs. One HTTPS call per attempt, OpenAI-compatible chat/completions.

Retry matrix (DOMAIN_MODEL.md §7):
    524            wait 125s, retry          (Cloudflare origin timeout)
    429            backoff honouring Retry-After
    timeout        retry once with compact context
    401/403        no retry -> advance chain
    404 model      advance chain             (free models rotate without notice)
    5xx            short backoff, retry
    parse failure  caller re-prompts with the error embedded
Sampling: attempt 1 temp=0 seed=42; each retry temp+=0.15 (cap 0.6), seed+=1.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import ModelEndpoint
from .events import Ev, Ledger


class ModelUnavailable(Exception):
    """This endpoint is dead for this run; try the next one."""


class ChainExhausted(Exception):
    """Every endpoint failed. Run degrades to read-only; never crashes the batch."""


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_calls: int = 12
    max_seconds: float = 25 * 60
    calls: int = 0
    seconds: float = 0.0

    def charge(self, seconds: float) -> None:
        self.calls += 1
        self.seconds += seconds
        if self.calls > self.max_calls:
            raise BudgetExceeded(f"model call cap {self.max_calls} reached")
        if self.seconds > self.max_seconds:
            raise BudgetExceeded(f"wall-clock budget {self.max_seconds:.0f}s exhausted")


def _http_transport(endpoint: ModelEndpoint, payload: dict) -> tuple[int, str]:
    """Default transport. Tests inject fakes; nothing else in this module does I/O."""
    headers = {
        "Content-Type": "application/json",
        # Cloudflare blocks urllib's default UA (error 1010); identify honestly
        "User-Agent": "oss-harness/0.3 (github.com/Mr-Neutr0n/oss-tracker)",
    }
    key = os.environ.get(endpoint.api_key_env, "") if endpoint.api_key_env else ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        f"{endpoint.base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=endpoint.timeout_s) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except TimeoutError:
        return 0, "timeout"
    except (urllib.error.URLError, OSError) as e:
        return -1, str(e)


class ProviderChain:
    def __init__(self, endpoints, ledger: Ledger | None = None,
                 transport=None, sleeper=time.sleep, clock=time.monotonic):
        # usable = anonymous (no api_key_env) or its key is actually present;
        # an endpoint that *declares* a key it doesn't have can never auth.
        self.endpoints = [e for e in endpoints
                          if not e.api_key_env or os.environ.get(e.api_key_env)] \
            if transport is None else list(endpoints)
        self.ledger = ledger
        self.transport = transport or _http_transport
        self.sleep = sleeper
        self.clock = clock
        self._dead: set[str] = set()

    @property
    def available(self) -> bool:
        return any(e.name not in self._dead for e in self.endpoints)

    def complete(self, prompt: str, *, purpose: str, subject: str = "",
                 budget: Budget | None = None, compact_prompt: str | None = None,
                 system: str = "", max_attempts: int = 3) -> str:
        for endpoint in self.endpoints:
            if endpoint.name in self._dead:
                continue
            try:
                return self._complete_on(endpoint, prompt, purpose=purpose, subject=subject,
                                         budget=budget, compact_prompt=compact_prompt,
                                         system=system, max_attempts=max_attempts)
            except ModelUnavailable as e:
                self._dead.add(endpoint.name)
                if self.ledger:
                    self.ledger.append(Ev.MODEL_CHAIN_ADVANCED, subject or "harness",
                                       endpoint=endpoint.name, error=str(e)[:200])
        raise ChainExhausted("no usable model endpoint")

    def _complete_on(self, ep: ModelEndpoint, prompt: str, *, purpose: str, subject: str,
                     budget: Budget | None, compact_prompt: str | None,
                     system: str, max_attempts: int) -> str:
        body = prompt
        for attempt in range(max_attempts):
            messages = ([{"role": "system", "content": system}] if system else []) + \
                       [{"role": "user", "content": body}]
            payload = {
                "model": ep.model,
                "messages": messages,
                "max_tokens": ep.max_tokens,
                "temperature": min(0.6, 0.15 * attempt),
                "seed": 42 + attempt,
            }
            t0 = self.clock()
            status, raw = self.transport(ep, payload)
            elapsed = self.clock() - t0
            if budget:
                budget.charge(elapsed)
            if self.ledger:
                self.ledger.append(Ev.MODEL_CALL, subject or "harness",
                                   purpose=purpose, endpoint=ep.name, attempt=attempt,
                                   status=status, latency_s=round(elapsed, 1))

            if status == 200:
                try:
                    text = json.loads(raw)["choices"][0]["message"]["content"]
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    if attempt + 1 < max_attempts:
                        continue
                    raise ModelUnavailable("malformed completion payload")
                if text and text.strip():
                    return text
                if attempt + 1 < max_attempts:
                    continue
                raise ModelUnavailable("empty completion")

            if status in (401, 403):
                raise ModelUnavailable(f"auth rejected ({status})")
            if status in (400, 404) and ("model" in raw.lower() or status == 404):
                raise ModelUnavailable(f"model gone ({status})")
            if status == 524:
                self._note(ep, "524 origin timeout; backing off 125s")
                self.sleep(125)
            elif status == 429:
                wait = _retry_after(raw, default=30 * (attempt + 1))
                self._note(ep, f"429 rate limited; waiting {wait:.0f}s")
                self.sleep(wait)
            elif status == 0:  # timeout: shrink context once, then bail to next endpoint
                self._note(ep, f"request timeout after {ep.timeout_s}s")
                if compact_prompt and body != compact_prompt:
                    body = compact_prompt
                elif attempt + 1 >= max_attempts:
                    raise ModelUnavailable("repeated timeouts")
            elif status >= 500 or status == -1:
                self._note(ep, f"transient {status}; retrying")
                self.sleep(10 * (attempt + 1))
            else:
                raise ModelUnavailable(f"unexpected status {status}")
        raise ModelUnavailable("attempts exhausted")

    @staticmethod
    def _note(ep: ModelEndpoint, msg: str) -> None:
        print(f"[model]   {ep.name}/{ep.model}: {msg}", flush=True)


def _retry_after(raw: str, default: float) -> float:
    try:
        return float(json.loads(raw).get("retry_after", default))
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError):
        return default
