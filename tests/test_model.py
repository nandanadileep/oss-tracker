import json

import pytest

from harness.config import ModelEndpoint
from harness.model import (Budget, BudgetExceeded, ChainExhausted,
                           ProviderChain)

EP1 = ModelEndpoint("primary", "https://x/v1", "m1", "KEY")
EP2 = ModelEndpoint("fallback", "https://y/v1", "m2", "KEY")


def _ok(text="PATCH OUTPUT"):
    return 200, json.dumps({"choices": [{"message": {"content": text}}]})


class Script:
    """Scripted transport: pops one (status, body) per call; records payloads."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, endpoint, payload):
        self.calls.append((endpoint.name, payload))
        return self.responses.pop(0)


def _chain(responses, sleeps=None):
    t = Script(responses)
    chain = ProviderChain([EP1, EP2], transport=t,
                          sleeper=(sleeps.append if sleeps is not None else lambda s: None),
                          clock=lambda: 0.0)
    return chain, t


def test_success_first_try_temp0_seed42():
    chain, t = _chain([_ok()])
    assert chain.complete("p", purpose="patch") == "PATCH OUTPUT"
    payload = t.calls[0][1]
    assert payload["temperature"] == 0.0 and payload["seed"] == 42


def test_524_waits_125s_then_retries_with_perturbed_sampling():
    sleeps = []
    chain, t = _chain([(524, "origin timeout"), _ok()], sleeps)
    assert chain.complete("p", purpose="patch") == "PATCH OUTPUT"
    assert sleeps == [125]
    assert t.calls[1][1]["temperature"] == pytest.approx(0.15)
    assert t.calls[1][1]["seed"] == 43


def test_429_honours_retry_after():
    sleeps = []
    chain, _ = _chain([(429, json.dumps({"retry_after": 7})), _ok()], sleeps)
    chain.complete("p", purpose="x")
    assert sleeps == [7]


def test_timeout_switches_to_compact_prompt():
    chain, t = _chain([(0, "timeout"), _ok()])
    chain.complete("LONG PROMPT", purpose="x", compact_prompt="SHORT")
    assert t.calls[0][1]["messages"][-1]["content"] == "LONG PROMPT"
    assert t.calls[1][1]["messages"][-1]["content"] == "SHORT"


def test_auth_failure_advances_chain():
    chain, t = _chain([(401, "bad key"), _ok("FROM FALLBACK")])
    assert chain.complete("p", purpose="x") == "FROM FALLBACK"
    assert [c[0] for c in t.calls] == ["primary", "fallback"]
    assert "primary" in chain._dead


def test_model_rotated_away_advances_chain():
    chain, t = _chain([(404, "model not found"), _ok("FB")])
    assert chain.complete("p", purpose="x") == "FB"


def test_chain_exhausted_raises_not_crashes_with_garbage():
    chain, _ = _chain([(401, "no"), (401, "no")])
    with pytest.raises(ChainExhausted):
        chain.complete("p", purpose="x")


def test_budget_caps_calls():
    b = Budget(max_calls=1, max_seconds=100)
    chain, _ = _chain([_ok(), _ok()])
    chain.complete("p", purpose="x", budget=b)
    with pytest.raises(BudgetExceeded):
        chain.complete("p", purpose="x", budget=b)


def test_dead_endpoint_skipped_on_next_call():
    chain, t = _chain([(403, "denied"), _ok("FB1"), _ok("FB2")])
    chain.complete("p", purpose="x")
    chain.complete("p", purpose="x")
    assert [c[0] for c in t.calls] == ["primary", "fallback", "fallback"]


def test_empty_completion_retries_then_advances():
    chain, _ = _chain([_ok(""), _ok(""), _ok(""), _ok("FB")])
    assert chain.complete("p", purpose="x") == "FB"


def test_anonymous_endpoints_usable_without_any_key(monkeypatch):
    monkeypatch.delenv("KEY", raising=False)
    anon = ModelEndpoint("anon", "https://x/v1", "free-model")  # no api_key_env
    keyed = ModelEndpoint("keyed", "https://y/v1", "paid-model", "KEY")
    chain = ProviderChain([anon, keyed])  # real-transport path: filters by key presence
    assert [e.name for e in chain.endpoints] == ["anon"]
    assert chain.available
    monkeypatch.setenv("KEY", "sk-x")
    assert [e.name for e in ProviderChain([anon, keyed]).endpoints] == ["anon", "keyed"]


def test_default_config_chain_is_anonymous():
    from harness.config import Config
    assert all(not e.api_key_env for e in Config().endpoints)
