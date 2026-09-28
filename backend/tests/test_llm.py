# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-19. Covers core/llm.py — the seam all six AI features now
# call. Every branch is exercised against a stubbed client, so nothing here
# touches the network or needs a key.
#
# The failure branches carry the weight. The whole point of the module is that a
# missing key, a bad key, a retired model and a malformed reply stop looking
# identical, so each one has a test asserting it is named correctly rather than
# collapsed into a generic error.
import json

import pytest

from app.core import llm
from app.core.config import settings


class _Msg:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content): self.message = _Msg(content)


class _Resp:
    def __init__(self, content): self.choices = [_Choice(content)]


class _FakeCompletions:
    def __init__(self, behaviour): self._b = behaviour; self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        out = self._b(kwargs, self.calls) if callable(self._b) else self._b
        if isinstance(out, Exception):
            raise out
        return _Resp(out)


class _FakeClient:
    def __init__(self, behaviour):
        self.chat = type("C", (), {"completions": _FakeCompletions(behaviour)})()


def _err(status_code=None, name="APIError", retry_after=None):
    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    response = type("R", (), {"status_code": status_code, "headers": headers})()
    return type(name, (Exception,), {})(f"simulated {name}")  # noqa: N806


def _err_with_response(status_code, retry_after=None):
    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    exc = Exception("simulated")
    exc.response = type("R", (), {"status_code": status_code, "headers": headers})()
    return exc


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Groq configured, an empty in-process store, and no sleeping between
    retries so the retry tests stay fast."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "test-model")
    # A real .env can set LLM_FALLBACK_PROVIDER; without clearing it here, a
    # failure this file deliberately provokes (bad key, 401, ...) could fall
    # through to a REAL provider instead of the failure this test expects.
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "LLM_CACHE_TTL_SECONDS", 60)
    monkeypatch.setattr(llm, "_store", llm._MemoryStore())
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    yield
    llm._store = None


def _use(monkeypatch, behaviour):
    client = _FakeClient(behaviour)
    monkeypatch.setattr(llm, "_client", lambda *_a, **_k: client)
    return client


# ── provider resolution ──────────────────────────────────────────────────────
def test_groq_selected_when_key_present():
    assert llm.resolved_provider() == ("groq", "test-model", "test-key")


def test_provider_none_when_key_missing(monkeypatch):
    """An empty key must degrade to "none", not produce a client that 401s on
    every call — this is the state the whole product was in."""
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    provider, _, _ = llm.resolved_provider()
    assert provider == "none"


def test_openai_provider_uses_its_own_model(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL_OPENAI", "gpt-4o-mini")
    assert llm.resolved_provider() == ("openai", "gpt-4o-mini", "sk-test")


# ── the happy path ───────────────────────────────────────────────────────────
def test_plain_completion(monkeypatch):
    _use(monkeypatch, "a written report")
    r = llm.complete("prompt", purpose="report")
    assert r.status == llm.OK
    assert r.text == "a written report"
    assert r.ai_generated is True
    assert r.model == "test-model"


def test_json_mode_parses(monkeypatch):
    _use(monkeypatch, json.dumps({"signal": "IMPROVING", "note": "up"}))
    r = llm.complete("p", purpose="insight", json_mode=True)
    assert r.status == llm.OK
    assert r.data["signal"] == "IMPROVING"


def test_json_mode_requests_json_format(monkeypatch):
    seen = {}

    def behaviour(kwargs, _n):
        seen.update(kwargs)
        return "{}"

    _use(monkeypatch, behaviour)
    llm.complete("p", purpose="x", json_mode=True)
    assert seen["response_format"] == {"type": "json_object"}


def test_system_prompt_is_sent_first(monkeypatch):
    seen = {}

    def behaviour(kwargs, _n):
        seen.update(kwargs)
        return "ok"

    _use(monkeypatch, behaviour)
    llm.complete("user text", purpose="x", system="you are an analyst")
    assert seen["messages"][0] == {"role": "system", "content": "you are an analyst"}
    assert seen["messages"][1]["content"] == "user text"


# ── failure classification — the reason this module exists ──────────────────
def test_missing_key_is_named_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    r = llm.complete("p", purpose="x")
    assert r.status == llm.NOT_CONFIGURED
    assert r.ai_generated is False
    assert r.failure_reason


def test_bad_key_is_auth_failed(monkeypatch):
    _use(monkeypatch, _err_with_response(401))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.AUTH_FAILED
    assert r.ai_generated is False


def test_retired_model_is_model_not_found(monkeypatch):
    """Groq retires models at short notice. A stale LLM_MODEL has to be
    distinguishable from every other failure or it is invisible."""
    _use(monkeypatch, _err_with_response(404))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.MODEL_NOT_FOUND


def test_server_side_json_rejection_is_bad_response(monkeypatch):
    """Groq validates JSON itself and returns 400 json_validate_failed when a
    reasoning model runs out of budget mid-object. Found live on 2026-08-19,
    where it was classified UPSTREAM_ERROR and retried three times — spending
    the rate limit to fail three ways at something retrying cannot fix."""
    client = _use(monkeypatch, _err_with_response(400))
    r = llm.complete("p", purpose="x", json_mode=True)
    assert r.status == llm.BAD_RESPONSE
    assert client.chat.completions.calls == 1        # not retried


def test_reasoning_effort_is_sent_when_configured(monkeypatch):
    """gpt-oss spends output budget thinking before answering. These prompts are
    structured extraction, not reasoning problems, so the effort is dialled down
    — which is both more reliable and measurably faster."""
    seen = {}

    def behaviour(kwargs, _n):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "low")
    _use(monkeypatch, behaviour)
    llm.complete("p", purpose="x")
    assert seen["extra_body"] == {"reasoning_effort": "low"}


def test_reasoning_effort_omitted_when_blank(monkeypatch):
    """Providers and models that do not accept the parameter must not receive
    it at all."""
    seen = {}

    def behaviour(kwargs, _n):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "")
    _use(monkeypatch, behaviour)
    llm.complete("p", purpose="x")
    assert "extra_body" not in seen


def test_rate_limit_is_named(monkeypatch):
    _use(monkeypatch, _err_with_response(429))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.RATE_LIMITED


def test_malformed_json_is_bad_response(monkeypatch):
    _use(monkeypatch, "here is your answer, not json")
    r = llm.complete("p", purpose="x", json_mode=True)
    assert r.status == llm.BAD_RESPONSE
    assert r.ai_generated is False
    assert r.text                       # the raw reply is kept for diagnosis


def test_unknown_failure_is_upstream_error(monkeypatch):
    _use(monkeypatch, RuntimeError("kaboom"))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.UPSTREAM_ERROR


# ── retries ──────────────────────────────────────────────────────────────────
def test_rate_limit_is_retried_then_succeeds(monkeypatch):
    def behaviour(_kwargs, n):
        return _err_with_response(429) if n == 1 else "recovered"

    client = _use(monkeypatch, behaviour)
    r = llm.complete("p", purpose="x")
    assert r.status == llm.OK and client.chat.completions.calls == 2


def test_auth_failure_is_not_retried(monkeypatch):
    """Retrying a bad key cannot fix it and just multiplies the failures."""
    client = _use(monkeypatch, _err_with_response(401))
    llm.complete("p", purpose="x")
    assert client.chat.completions.calls == 1


def test_model_not_found_is_not_retried(monkeypatch):
    client = _use(monkeypatch, _err_with_response(404))
    llm.complete("p", purpose="x")
    assert client.chat.completions.calls == 1


def test_retries_are_bounded(monkeypatch):
    client = _use(monkeypatch, _err_with_response(429))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.RATE_LIMITED
    assert client.chat.completions.calls == settings.LLM_MAX_RETRIES + 1


# ── caching ──────────────────────────────────────────────────────────────────
def test_identical_prompt_is_served_from_cache(monkeypatch):
    client = _use(monkeypatch, "expensive answer")
    first = llm.complete("same prompt", purpose="p1")
    second = llm.complete("same prompt", purpose="p1")
    assert first.status == llm.OK and second.status == llm.CACHED
    assert second.text == "expensive answer"
    assert second.cached is True
    assert client.chat.completions.calls == 1        # billed once, not twice


def test_cached_answer_still_counts_as_ai_generated(monkeypatch):
    _use(monkeypatch, "answer")
    llm.complete("p", purpose="p2")
    assert llm.complete("p", purpose="p2").ai_generated is True


def test_different_prompt_is_a_separate_entry(monkeypatch):
    client = _use(monkeypatch, "answer")
    llm.complete("prompt A", purpose="p3")
    llm.complete("prompt B", purpose="p3")
    assert client.chat.completions.calls == 2


def test_same_prompt_different_purpose_is_not_shared(monkeypatch):
    """Two features asking the same question want their own answers — sharing
    would leak one feature's phrasing into another."""
    client = _use(monkeypatch, "answer")
    llm.complete("p", purpose="alpha")
    llm.complete("p", purpose="beta")
    assert client.chat.completions.calls == 2


def test_cache_can_be_disabled_per_call(monkeypatch):
    client = _use(monkeypatch, "answer")
    llm.complete("p", purpose="p4", cache_ttl=0)
    llm.complete("p", purpose="p4", cache_ttl=0)
    assert client.chat.completions.calls == 2


def test_failures_are_never_cached(monkeypatch):
    """Caching a failure would keep a feature dead long after the key is fixed."""
    calls = {"n": 0}

    def behaviour(_kwargs, n):
        calls["n"] = n
        return _err_with_response(500) if n <= 3 else "works now"

    client = _use(monkeypatch, behaviour)
    assert llm.complete("p", purpose="p5").ai_generated is False
    assert llm.complete("p", purpose="p5").status == llm.OK
    assert client.chat.completions.calls > 1


# ── counters and health ──────────────────────────────────────────────────────
def test_counters_record_per_purpose(monkeypatch):
    _use(monkeypatch, "answer")
    llm.complete("p", purpose="briefing")
    counters = llm.stats()
    assert counters.get("briefing", {}).get(llm.OK) == 1


def test_health_reports_provider_without_leaking_the_key():
    h = llm.health()
    assert h["active_provider"] == "groq"
    assert h["model"] == "test-model"
    assert h["key_present"] is True
    assert "test-key" not in json.dumps(h)


def test_health_flags_unusable_when_no_key(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    h = llm.health()
    assert h["usable"] is False and h["key_present"] is False
