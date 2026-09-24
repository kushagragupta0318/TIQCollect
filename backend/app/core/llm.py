# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-19 — New file. Six product features called an LLM directly, each with
#   its own inline client, its own hardcoded model="gpt-4o-mini", and its own
#   `except Exception: pass`. With OPENAI_API_KEY empty every one of them
#   quietly served a written-in answer instead, and nothing anywhere recorded
#   that it had. A missing key, an expired subscription, a rate limit and a
#   malformed reply were indistinguishable from the outside — they all looked
#   like the feature working normally.
#
#   This is the single seam they now share. Deliberately the same shape as
#   core/transcription.py, which already does provider selection for Whisper:
#   one contract, provider chosen by a settings value, documented here.
#
#   The contract is that callers NEVER see an exception. They get an LLMResult
#   whose ai_generated flag says whether a model actually produced the text, so
#   the fallback can be shown and labelled rather than passed off as AI.
#
# 2026-09-24 — F01 (standalone plan §8.1): an Anthropic provider, tool calling,
#   a fallback provider and a scripted fake. complete() keeps its signature and
#   its groq/openai behaviour; everything new sits beside it.
#
#   - provider "anthropic" through the official `anthropic` SDK. Two tiers:
#     complete() uses LLM_MODEL_ANTHROPIC (Haiku 4.5, short product prompts),
#     chat() uses LLM_MODEL_ANTHROPIC_AGENT (Sonnet 5, tool-using agents).
#   - chat(): ONE model turn with tools, in a provider-neutral message format,
#     translated to Anthropic content blocks or OpenAI tool_calls. The loop,
#     guardrails and tool execution belong to the agent runtime (F02), not here:
#     a seam that ran tools would be a second place deciding what an agent may
#     do. Never cached — a tool loop is stateful and may carry write tools.
#   - LLM_FALLBACK_PROVIDER: tried once when the primary is unusable or fails on
#     its own side. Never on a caller-shaped failure (bad JSON, invalid request,
#     refusal), which another model would not make right.
#   - FakeLLM + use_fake(): a scripted provider that goes through the same retry,
#     classification and counter code, so F02's tests exercise the real seam
#     with no network and no key.
#   - json_schema= on complete(): structured outputs on Anthropic; on groq the
#     schema rides in the system prompt. Opt-in — without it the groq request
#     is byte-identical, pinned by test_existing_json_mode_call_sends_exactly_
#     what_it_did.
#   - Four new statuses: INVALID_REQUEST (an Anthropic 400 is a parameter the
#     model rejects, not the Groq JSON case), BILLING (402), REFUSED
#     (stop_reason "refusal").
#   - CHANGED on the groq/openai path, visibly: a reply that hit max_tokens
#     with NO text (finish_reason "length", content "") USED TO return OK with
#     an empty string, so ai_generated read True over nothing — the monthly
#     report endpoint then showed its computed fallback text while reporting
#     "ai_generated": true. It is now BAD_RESPONSE. Every caller gates on
#     ai_generated (agent.py visit strategy, manager.py briefing / insight /
#     monthly report, ai_report_service, case_service rank reasons), so all six
#     take their labelled fallback exactly as for any other failure.
#
#   Found on the way, and fixed: the openai SDK retries 429/5xx itself
#   (DEFAULT_MAX_RETRIES = 2 in 1.57.4, verified in the fieldops-dev image)
#   UNDER this module's own LLM_MAX_RETRIES = 2. MEASURED against a local
#   server answering 429 to everything, one complete() on groq made 9 HTTP
#   requests while this module logged 3 llm.failed lines — six requests it
#   never saw, never counted, and never applied its Retry-After handling to.
#   Every SDK client is now built with max_retries=0: 3 requests, 3 log lines.
#   The anthropic SDK (1.8.0) has the same default and measured the same, 9
#   before and 3 after.
# ───────────────────────────────────────────────────────────────────────────
"""One LLM entry point for the whole product.

Providers, selected via settings.LLM_PROVIDER:
  - "groq"      (default) — OpenAI-compatible, so the same `openai` SDK is used
                            with base_url pointed at Groq. Needs GROQ_API_KEY.
  - "openai"              — the original hosted path. Needs OPENAI_API_KEY.
  - "anthropic"           — Claude, through the `anthropic` SDK. Needs
                            ANTHROPIC_API_KEY.
  - "none"                — never calls out; every request reports
                            NOT_CONFIGURED. Useful for demos and tests.

Two calls:
  - complete(prompt, ...) -> LLMResult   single-shot text or JSON, cached.
  - chat(messages, tools=...) -> ChatResult   one tool-calling turn, uncached.

Nothing here raises. Every outcome is classified, logged with its `purpose`,
and returned.

Neutral message format for chat() — the same dicts work for every provider:

    {"role": "user", "content": "text"}
    {"role": "assistant", "content": "text", "tool_calls": [ToolCall, ...]}
        (use ChatResult.assistant_message() — it also carries the provider's
         raw content, which Anthropic needs back verbatim when thinking ran)
    {"role": "tool", "tool_call_id": "...", "content": "text", "is_error": False}
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import structlog

from app.core.config import settings

logger = structlog.get_logger()

# ── Outcomes ─────────────────────────────────────────────────────────────────
OK = "OK"
CACHED = "CACHED"
NOT_CONFIGURED = "NOT_CONFIGURED"   # no key, or provider is "none"
AUTH_FAILED = "AUTH_FAILED"         # 401/403 — wrong or revoked key
BILLING = "BILLING"                 # 402 — the key is fine, the account cannot pay
RATE_LIMITED = "RATE_LIMITED"       # 429 — survived the retries
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"  # 404 — LLM_MODEL is wrong or retired
TIMEOUT = "TIMEOUT"
BAD_RESPONSE = "BAD_RESPONSE"       # answered, but not the JSON we asked for
INVALID_REQUEST = "INVALID_REQUEST"  # Anthropic 400 — a parameter this model rejects
REFUSED = "REFUSED"                 # the model declined (stop_reason "refusal")
UPSTREAM_ERROR = "UPSTREAM_ERROR"   # anything else

# Retrying cannot fix a bad key or an unknown model, and retrying a timeout on a
# request a user is waiting for just doubles the wait.
_RETRYABLE = {RATE_LIMITED, UPSTREAM_ERROR}

# Failures that belong to the PROVIDER, so a different provider might succeed.
# BAD_RESPONSE, INVALID_REQUEST and REFUSED are about the request or the answer;
# sending the same thing elsewhere hides the problem instead of fixing it.
_FALLBACK_ON = {NOT_CONFIGURED, AUTH_FAILED, BILLING, RATE_LIMITED,
                MODEL_NOT_FOUND, TIMEOUT, UPSTREAM_ERROR}

_PROVIDERS = ("groq", "openai", "anthropic")


@dataclass
class LLMResult:
    """What a caller gets back. Never an exception."""
    status: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    cached: bool = False
    failure_reason: str | None = None
    # Normalised across providers: end_turn | tool_use | max_tokens | refusal.
    # Empty when the provider did not say (or on a cache hit).
    stop_reason: str = ""
    # Set when the answer came from LLM_FALLBACK_PROVIDER: the provider that
    # failed first, so "the AI works" is never mistaken for "the primary works".
    fallback_from: str | None = None

    @property
    def ai_generated(self) -> bool:
        """True only when a model actually produced this. A cached answer still
        counts — it was generated by a model, just not on this request."""
        return self.status in (OK, CACHED)


# ── chat() types ─────────────────────────────────────────────────────────────
@dataclass
class ToolSpec:
    """A tool the model may call. `parameters` is a JSON Schema object.

    `strict` asks Anthropic to guarantee schema-valid arguments; it requires
    `additionalProperties: false` and a `required` list in the schema. Ignored
    by OpenAI-compatible providers, whose strict mode differs per model.
    """
    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    # OpenAI-compatible providers return arguments as a JSON STRING. When it does
    # not parse, `arguments` is {} and this says why — the runtime should answer
    # the call with an error result rather than run the tool on nothing.
    arguments_error: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class ChatResult:
    """One model turn. Never an exception."""
    status: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    usage: Usage = field(default_factory=Usage)
    failure_reason: str | None = None
    fallback_from: str | None = None
    # The provider's own content blocks, as plain dicts. Anthropic requires
    # thinking blocks to be sent back unchanged on the next turn of a tool loop;
    # a text + tool_calls reconstruction would silently drop them.
    provider_content: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ai_generated(self) -> bool:
        return self.status == OK

    @property
    def wants_tools(self) -> bool:
        return self.status == OK and bool(self.tool_calls)

    def assistant_message(self) -> dict[str, Any]:
        """This turn as a neutral assistant message, ready to append."""
        return {
            "role": "assistant",
            "content": self.text,
            "tool_calls": list(self.tool_calls),
            "provider": self.provider,
            "model": self.model,
            "provider_content": list(self.provider_content),
        }


def tool_result(call: ToolCall | str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    """The neutral message answering one tool call."""
    call_id = call.id if isinstance(call, ToolCall) else call
    return {"role": "tool", "tool_call_id": call_id, "content": content, "is_error": is_error}


# ── Provider wiring ──────────────────────────────────────────────────────────
_fake: "FakeLLM | None" = None


def _resolve(name: str, tier: str) -> tuple[str, str, str]:
    """(provider, model, api_key) for one named provider. "none" when unusable."""
    p = (name or "none").strip().lower()
    if p == "groq":
        return ("groq" if settings.GROQ_API_KEY else "none",
                settings.LLM_MODEL, settings.GROQ_API_KEY)
    if p == "openai":
        return ("openai" if settings.OPENAI_API_KEY else "none",
                settings.LLM_MODEL_OPENAI, settings.OPENAI_API_KEY)
    if p == "anthropic":
        model = (settings.LLM_MODEL_ANTHROPIC_AGENT if tier == "agent"
                 else settings.LLM_MODEL_ANTHROPIC)
        return ("anthropic" if settings.ANTHROPIC_API_KEY else "none",
                model, settings.ANTHROPIC_API_KEY)
    return ("none", "", "")


def resolved_provider(tier: str = "fast") -> tuple[str, str, str]:
    """(provider, model, api_key) for the configured primary provider.

    `tier` is "fast" (complete) or "agent" (chat). Only Anthropic has two
    models; groq and openai answer both tiers with their one configured model.
    """
    if _fake is not None:
        return ("fake", _fake.model, "fake")
    return _resolve(settings.LLM_PROVIDER, tier)


def _candidates(tier: str) -> list[tuple[str, str, str]]:
    """Primary, then the fallback if one is configured and differs."""
    first = resolved_provider(tier)
    out = [first]
    fb = (settings.LLM_FALLBACK_PROVIDER or "").strip().lower()
    if _fake is None and fb and fb != (settings.LLM_PROVIDER or "").strip().lower():
        out.append(_resolve(fb, tier))
    return out


def _client(provider: str, api_key: str):
    from openai import OpenAI
    # max_retries=0: this module owns retries (see the 2026-09-24 note above).
    if provider == "groq":
        # Groq speaks the OpenAI wire protocol, so no new dependency is needed —
        # only a different base_url.
        return OpenAI(api_key=api_key, base_url=settings.GROQ_BASE_URL,
                      timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0)
    return OpenAI(api_key=api_key, timeout=settings.LLM_TIMEOUT_SECONDS, max_retries=0)


def _anthropic_client(api_key: str):
    import anthropic
    return anthropic.Anthropic(api_key=api_key, timeout=settings.LLM_TIMEOUT_SECONDS,
                               max_retries=0)


def _classify(exc: Exception, provider: str = "") -> str:
    """Map an SDK exception onto one of our statuses.

    Matched on status_code first and class name second, rather than by importing
    the SDK's exception classes, so a minor SDK version bump cannot silently
    turn every failure into UPSTREAM_ERROR. Works for the openai and anthropic
    SDKs alike: both carry `status_code` and `response` on their status errors.
    """
    code = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
    if code == 400:
        if provider == "anthropic":
            # Anthropic validates requests strictly: a 400 is a parameter this
            # model does not accept (temperature on Sonnet 5, effort on Haiku
            # 4.5, a malformed tool schema). Not transient, and not the model
            # answering badly — naming it BAD_RESPONSE would send someone
            # looking at the prompt instead of the request shape.
            return INVALID_REQUEST
        # Groq validates JSON server-side and returns 400 json_validate_failed
        # when a reasoning model runs out of budget mid-object. That is a
        # response-shape problem, not a transient one — retrying it three times
        # only spends the rate limit to fail three ways.
        return BAD_RESPONSE
    if code == 401 or code == 403:
        return AUTH_FAILED
    if code == 402:
        return BILLING
    if code == 404:
        return MODEL_NOT_FOUND
    if code == 429:
        return RATE_LIMITED
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return TIMEOUT
    if "authentication" in name or "permissiondenied" in name:
        return AUTH_FAILED
    if "notfound" in name:
        return MODEL_NOT_FOUND
    if "ratelimit" in name:
        return RATE_LIMITED
    return UPSTREAM_ERROR


# ── Anthropic request shape, per model family ────────────────────────────────
# Three parameters are accepted by some Claude models and rejected with a 400 by
# others, so they are decided here, once, from the model id:
#   temperature — removed from Opus 4.7 onward, Sonnet 5, Opus 5.x and Fable /
#                 Mythos; accepted by Haiku 4.5 and the 4.6 generation. The
#                 anthropic 1.x SDK dropped it from messages.create() entirely
#                 (a TypeError — caught by the real-SDK test, not by a stub),
#                 so where the model still honours it, it rides in extra_body,
#                 which the SDK merges into the request JSON as-is.
#   effort      — output_config.effort; errors on Haiku 4.5 and Sonnet 4.5.
#   thinking    — ON by default (adaptive) on Sonnet 5, Opus 5.x, Fable, Mythos,
#                 and thinking tokens count against max_tokens.
_NO_SAMPLING = ("sonnet-5", "opus-5", "opus-4-7", "opus-4-8", "fable", "mythos")
_EFFORT_ALSO = ("opus-4-5", "opus-4-6", "sonnet-4-6")
_THINKS_BY_DEFAULT = ("sonnet-5", "opus-5", "fable", "mythos")
# complete() callers size max_tokens for the ANSWER (400 by default). On a model
# that thinks by default that budget would be spent before the answer starts, so
# a thinking model gets this much extra ceiling. A ceiling, not a spend: unused
# tokens are not billed.
THINKING_HEADROOM_TOKENS = 4096


def anthropic_caps(model: str) -> dict[str, bool]:
    m = (model or "").lower()
    no_sampling = any(k in m for k in _NO_SAMPLING)
    return {
        "temperature": not no_sampling,
        "effort": no_sampling or any(k in m for k in _EFFORT_ALSO),
        "thinks_by_default": any(k in m for k in _THINKS_BY_DEFAULT),
    }


_JSON_INSTRUCTION = ("Respond with a single JSON object and nothing else — no prose, "
                     "no code fences.")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _stop(raw: str | None) -> str:
    """Normalise a provider's stop/finish reason."""
    return {
        "end_turn": "end_turn", "stop_sequence": "end_turn", "stop": "end_turn",
        "tool_use": "tool_use", "tool_calls": "tool_use", "function_call": "tool_use",
        "max_tokens": "max_tokens", "length": "max_tokens",
        "refusal": "refusal", "content_filter": "refusal",
        "pause_turn": "pause_turn",
    }.get(raw or "", raw or "")


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    return {k: v for k, v in vars(block).items() if not k.startswith("_")}


def _usage_from(u: Any) -> Usage:
    if u is None:
        return Usage()
    g = (lambda *names: next((int(getattr(u, n)) for n in names
                              if isinstance(getattr(u, n, None), (int, float))), 0))
    return Usage(
        input_tokens=g("input_tokens", "prompt_tokens"),
        output_tokens=g("output_tokens", "completion_tokens"),
        cache_read_input_tokens=g("cache_read_input_tokens"),
        cache_creation_input_tokens=g("cache_creation_input_tokens"),
    )


# ── Cache + failure counters (Redis, with an in-process fallback) ─────────────
class _MemoryStore:
    """Used when Redis is unreachable, so the seam works with no infrastructure
    — the same approach otp_service takes."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, str]] = {}

    def get(self, k: str) -> str | None:
        hit = self._d.get(k)
        if not hit:
            return None
        expires, val = hit
        if expires < time.time():
            self._d.pop(k, None)
            return None
        return val

    def setex(self, k: str, ttl: int, v: str) -> None:
        self._d[k] = (time.time() + ttl, v)

    def incr(self, k: str) -> int:
        cur = int(self.get(k) or 0) + 1
        self.setex(k, 86400, str(cur))
        return cur

    def expire(self, k: str, ttl: int) -> None:  # pragma: no cover - parity only
        pass


_store = None


def _get_store():
    global _store
    if _store is None:
        try:
            import redis
            c = redis.from_url(settings.REDIS_URL, decode_responses=True,
                               socket_connect_timeout=0.5)
            c.ping()
            _store = c
        except Exception:
            _store = _MemoryStore()
    return _store


def reset_store_for_tests() -> None:
    """Tests inject their own store; production never calls this."""
    global _store
    _store = None


def _cache_key(purpose: str, model: str, prompt: str, system: str | None,
               schema: dict | None = None) -> str:
    raw = f"{model}\x00{system or ''}\x00{prompt}"
    if schema is not None:
        # A schema changes the answer, so it is part of the question. Omitted
        # when absent so every pre-existing key is unchanged.
        raw += "\x00" + json.dumps(schema, sort_keys=True)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return f"llm:cache:{purpose}:{digest}"


def _record(purpose: str, status: str) -> None:
    """Rolling per-purpose counters, so "the AI is broken" becomes answerable:
    which feature, and failing how."""
    try:
        store = _get_store()
        key = f"llm:stat:{purpose}:{status}"
        store.incr(key)
        store.expire(key, 86400)
    except Exception:
        pass       # telemetry must never break the request it is measuring


def stats() -> dict[str, dict[str, int]]:
    """Counters by purpose, for GET /manager/ai/health."""
    out: dict[str, dict[str, int]] = {}
    try:
        store = _get_store()
        if isinstance(store, _MemoryStore):
            keys = [k for k in store._d if k.startswith("llm:stat:")]
            getter = store.get
        else:
            keys = list(store.scan_iter("llm:stat:*"))
            getter = store.get
        for k in keys:
            _, _, purpose, status = k.split(":", 3)
            out.setdefault(purpose, {})[status] = int(getter(k) or 0)
    except Exception:
        pass
    return out


# ── Retry loop, shared by both calls and every provider ──────────────────────
def _attempts(purpose: str, provider: str, model: str, fn: Callable[[], Any]):
    """Run fn with the retry policy. Returns (value, None) or (None, (status, detail))."""
    last_status, last_detail = UPSTREAM_ERROR, None
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            return fn(), None
        except Exception as exc:
            last_status = _classify(exc, provider)
            last_detail = str(exc)[:200]
            retryable = last_status in _RETRYABLE and attempt < settings.LLM_MAX_RETRIES
            logger.warning("llm.failed", purpose=purpose, provider=provider, model=model,
                           status=last_status, attempt=attempt, will_retry=retryable,
                           error=last_detail)
            if not retryable:
                break
            # Honour Retry-After when the provider sends one — Groq does, and
            # ignoring it is how a rate limit becomes a ban.
            wait = _retry_after(exc)
            time.sleep(min(wait if wait is not None else 0.5 * (2 ** attempt), 5.0))
    return None, (last_status, last_detail)


# ── complete(): one-shot text or JSON ────────────────────────────────────────
def _complete_openai(provider, model, api_key, prompt, system, json_mode, json_schema,
                     max_tokens, temperature) -> tuple[str, str]:
    messages: list[dict] = []
    sys_text = system
    if json_schema is not None:
        # json_object is what every OpenAI-compatible model here honours;
        # json_schema support varies by Groq model. So the schema rides in the
        # system prompt and json_object enforces "an object".
        sys_text = ((system + "\n\n") if system else "") + \
            "The JSON object must match this JSON Schema:\n" + json.dumps(json_schema)
    if sys_text:
        messages.append({"role": "system", "content": sys_text})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": model, "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature,
    }
    if json_mode or json_schema is not None:
        kwargs["response_format"] = {"type": "json_object"}
    if settings.LLM_REASONING_EFFORT:
        # Not a typed parameter on openai==1.57.4, so it rides in extra_body.
        # A provider that does not recognise it ignores it.
        kwargs["extra_body"] = {"reasoning_effort": settings.LLM_REASONING_EFFORT}
    resp = _client(provider, api_key).chat.completions.create(**kwargs)
    choice = resp.choices[0]
    return (choice.message.content or "").strip(), _stop(getattr(choice, "finish_reason", None))


def _complete_anthropic(model, api_key, prompt, system, json_mode, json_schema,
                        max_tokens, temperature) -> tuple[str, str, str | None]:
    caps = anthropic_caps(model)
    sys_text = system
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens + (THINKING_HEADROOM_TOKENS if caps["thinks_by_default"] else 0),
    }
    if json_schema is not None:
        # Structured outputs: the API guarantees the text is valid JSON for it.
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
    elif json_mode:
        # No schema to constrain against, so ask for it and parse — the same
        # check the openai path applies, and BAD_RESPONSE when it is not JSON.
        sys_text = ((system + "\n\n") if system else "") + _JSON_INSTRUCTION
    if sys_text:
        kwargs["system"] = sys_text
    if caps["temperature"]:
        kwargs["extra_body"] = {"temperature": temperature}
    if caps["effort"] and settings.LLM_REASONING_EFFORT:
        # The same "these are extraction prompts, not reasoning problems" dial
        # LLM_REASONING_EFFORT sets for gpt-oss, on the models that accept it.
        kwargs.setdefault("output_config", {})["effort"] = settings.LLM_REASONING_EFFORT
    resp = _anthropic_client(api_key).messages.create(**kwargs)
    text = "".join(getattr(b, "text", "") or "" for b in resp.content
                   if getattr(b, "type", None) == "text").strip()
    stop = _stop(getattr(resp, "stop_reason", None))
    refusal = None
    if stop == "refusal":
        det = getattr(resp, "stop_details", None)
        refusal = f"refused ({getattr(det, 'category', None) or 'unspecified'})"
    return text, stop, refusal


def complete(
    prompt: str,
    *,
    purpose: str,
    system: str | None = None,
    json_mode: bool = False,
    max_tokens: int = 400,
    temperature: float = 0.3,
    cache_ttl: int | None = None,
    json_schema: dict | None = None,
) -> LLMResult:
    """Ask the model. Returns an LLMResult; never raises.

    `purpose` is a short stable label for the feature making the call
    ("visit_strategy", "briefing", ...). It keys the logs, the counters and the
    cache, and is what turns a vague "the AI is down" into a specific answer.

    `json_schema` (optional) implies json_mode. On Anthropic it is enforced by
    the API (structured outputs); on OpenAI-compatible providers it is given to
    the model and the reply is still parsed and checked for an object.
    """
    started = time.monotonic()
    want_json = json_mode or json_schema is not None
    first_failure: str | None = None
    result: LLMResult | None = None

    for provider, model, api_key in _candidates("fast"):
        result = _complete_one(provider, model, api_key, prompt, purpose=purpose,
                               system=system, want_json=want_json, json_schema=json_schema,
                               max_tokens=max_tokens, temperature=temperature,
                               cache_ttl=cache_ttl, started=started)
        if result.status not in _FALLBACK_ON:
            break
        first_failure = first_failure or (provider if provider != "none" else settings.LLM_PROVIDER)
    assert result is not None
    if first_failure and result.ai_generated:
        result.fallback_from = first_failure
        logger.warning("llm.fallback_used", purpose=purpose, failed=first_failure,
                       served_by=result.provider)
    return result


def _complete_one(provider, model, api_key, prompt, *, purpose, system, want_json,
                  json_schema, max_tokens, temperature, cache_ttl, started) -> LLMResult:
    if provider == "none":
        _record(purpose, NOT_CONFIGURED)
        logger.warning("llm.not_configured", purpose=purpose,
                       configured_provider=settings.LLM_PROVIDER)
        return LLMResult(status=NOT_CONFIGURED, provider="none",
                         failure_reason="No API key configured for the selected provider")

    ttl = settings.LLM_CACHE_TTL_SECONDS if cache_ttl is None else cache_ttl
    key = _cache_key(purpose, model, prompt, system, json_schema)
    if ttl > 0:
        try:
            hit = _get_store().get(key)
        except Exception:
            hit = None
        if hit:
            payload = json.loads(hit)
            _record(purpose, CACHED)
            return LLMResult(
                status=CACHED, text=payload.get("text", ""), data=payload.get("data") or {},
                provider=provider, model=model, cached=True,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    def call() -> tuple[str, str, str | None]:
        if provider == "fake":
            return _fake._complete(prompt=prompt, system=system, purpose=purpose,
                                   json_mode=want_json, json_schema=json_schema)
        if provider == "anthropic":
            return _complete_anthropic(model, api_key, prompt, system, want_json,
                                       json_schema, max_tokens, temperature)
        text, stop = _complete_openai(provider, model, api_key, prompt, system, want_json,
                                      json_schema, max_tokens, temperature)
        return text, stop, None

    value, failure = _attempts(purpose, provider, model, call)
    latency = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
    if failure:
        status, detail = failure
        _record(purpose, status)
        return LLMResult(status=status, provider=provider, model=model,
                         failure_reason=detail, latency_ms=latency())

    text, stop, refusal = value
    if refusal:
        _record(purpose, REFUSED)
        logger.warning("llm.refused", purpose=purpose, provider=provider, model=model,
                       reason=refusal)
        return LLMResult(status=REFUSED, provider=provider, model=model, text=text,
                         stop_reason=stop, failure_reason=refusal, latency_ms=latency())
    if stop == "max_tokens" and not text:
        # The whole budget went on reasoning and no answer was started — the
        # gpt-oss failure LLM_REASONING_EFFORT exists for, seen from this side.
        _record(purpose, BAD_RESPONSE)
        logger.warning("llm.no_answer_in_budget", purpose=purpose, provider=provider,
                       model=model)
        return LLMResult(status=BAD_RESPONSE, provider=provider, model=model,
                         stop_reason=stop, latency_ms=latency(),
                         failure_reason="Output budget ran out before an answer was written")

    data: dict[str, Any] = {}
    if want_json:
        try:
            parsed = json.loads(_strip_fences(text))
        except (ValueError, TypeError) as exc:
            # The model answered but not in the shape we asked for. A
            # weaker model drifting on a strict JSON contract lands here,
            # which is exactly what we want visible rather than swallowed.
            _record(purpose, BAD_RESPONSE)
            logger.warning("llm.bad_response", purpose=purpose, provider=provider,
                           model=model, error=str(exc), preview=text[:120])
            return LLMResult(status=BAD_RESPONSE, provider=provider, model=model,
                             text=text, stop_reason=stop,
                             failure_reason="Model did not return valid JSON",
                             latency_ms=latency())
        data = parsed if isinstance(parsed, dict) else {"value": parsed}

    if ttl > 0:
        try:
            _get_store().setex(key, ttl, json.dumps({"text": text, "data": data}))
        except Exception:
            pass
    _record(purpose, OK)
    logger.info("llm.ok", purpose=purpose, provider=provider, model=model,
                latency_ms=latency(), stop_reason=stop, cached=False)
    return LLMResult(status=OK, text=text, data=data, provider=provider,
                     model=model, stop_reason=stop, latency_ms=latency())


# ── chat(): one tool-calling turn ────────────────────────────────────────────
def _to_anthropic(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    pending_results: list[dict] = []

    def flush() -> None:
        # Every tool_result of one turn goes back in ONE user message. Splitting
        # them trains the model out of parallel tool calls.
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                     "content": str(m.get("content", ""))}
            if m.get("is_error"):
                block["is_error"] = True
            pending_results.append(block)
            continue
        flush()
        if role == "assistant":
            if m.get("provider") == "anthropic" and m.get("provider_content"):
                out.append({"role": "assistant", "content": list(m["provider_content"])})
                continue
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                tc = tc if isinstance(tc, ToolCall) else ToolCall(**tc)
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name,
                               "input": tc.arguments})
            out.append({"role": "assistant", "content": blocks or ""})
        elif role == "user":
            out.append({"role": "user", "content": m.get("content", "")})
    flush()
    return out


def _to_openai(system: str | None, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            content = str(m.get("content", ""))
            # No is_error field on this wire format, so the model is told in text.
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                        "content": ("ERROR: " + content) if m.get("is_error") else content})
        elif role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
            calls = [tc if isinstance(tc, ToolCall) else ToolCall(**tc)
                     for tc in (m.get("tool_calls") or [])]
            if calls:
                msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                      "function": {"name": tc.name,
                                                   "arguments": json.dumps(tc.arguments)}}
                                     for tc in calls]
            out.append(msg)
        elif role == "user":
            out.append({"role": "user", "content": m.get("content", "")})
    return out


def _chat_anthropic(model, api_key, system, messages, tools, max_tokens, effort,
                    temperature) -> ChatResult:
    caps = anthropic_caps(model)
    kwargs: dict[str, Any] = {
        "model": model, "max_tokens": max_tokens,
        "messages": _to_anthropic(messages),
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters,
             **({"strict": True} if t.strict else {})}
            for t in tools
        ]
    if caps["temperature"] and temperature is not None:
        kwargs["extra_body"] = {"temperature": temperature}
    if caps["effort"] and effort:
        kwargs["output_config"] = {"effort": effort}
    resp = _anthropic_client(api_key).messages.create(**kwargs)

    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for b in resp.content:
        t = getattr(b, "type", None)
        if t == "text":
            text_parts.append(getattr(b, "text", "") or "")
        elif t == "tool_use":
            args = getattr(b, "input", None)
            calls.append(ToolCall(id=b.id, name=b.name,
                                  arguments=args if isinstance(args, dict) else {}))
    stop = _stop(getattr(resp, "stop_reason", None))
    res = ChatResult(status=OK, text="".join(text_parts).strip(), tool_calls=calls,
                     stop_reason=stop, provider="anthropic", model=model,
                     usage=_usage_from(getattr(resp, "usage", None)),
                     provider_content=[_block_dict(b) for b in resp.content])
    if stop == "refusal":
        det = getattr(resp, "stop_details", None)
        res.status = REFUSED
        res.failure_reason = f"refused ({getattr(det, 'category', None) or 'unspecified'})"
    return res


def _chat_openai(provider, model, api_key, system, messages, tools, max_tokens,
                 temperature) -> ChatResult:
    kwargs: dict[str, Any] = {
        "model": model, "messages": _to_openai(system, messages), "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = [{"type": "function",
                            "function": {"name": t.name, "description": t.description,
                                         "parameters": t.parameters}} for t in tools]
    if settings.LLM_REASONING_EFFORT:
        kwargs["extra_body"] = {"reasoning_effort": settings.LLM_REASONING_EFFORT}
    resp = _client(provider, api_key).chat.completions.create(**kwargs)
    choice = resp.choices[0]
    msg = choice.message
    calls: list[ToolCall] = []
    for tc in getattr(msg, "tool_calls", None) or []:
        raw = tc.function.arguments or "{}"
        try:
            args = json.loads(raw)
            err = None if isinstance(args, dict) else "arguments were not a JSON object"
            args = args if isinstance(args, dict) else {}
        except (ValueError, TypeError) as exc:
            args, err = {}, f"arguments were not valid JSON: {exc}"
        calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args,
                              arguments_error=err))
    stop = _stop(getattr(choice, "finish_reason", None))
    res = ChatResult(status=OK, text=(msg.content or "").strip(), tool_calls=calls,
                     stop_reason=stop, provider=provider, model=model,
                     usage=_usage_from(getattr(resp, "usage", None)))
    if stop == "refusal":
        res.status, res.failure_reason = REFUSED, "refused (content_filter)"
    return res


def chat(
    messages: list[dict],
    *,
    purpose: str,
    system: str | None = None,
    tools: list[ToolSpec] | None = None,
    max_tokens: int = 16000,
    effort: str | None = None,
    temperature: float | None = None,
) -> ChatResult:
    """One model turn with tools. Returns a ChatResult; never raises.

    The caller runs the loop: execute `result.tool_calls`, append
    `result.assistant_message()` and one `tool_result(...)` per call, and call
    chat() again until `result.wants_tools` is False. Nothing is cached.

    `effort` overrides LLM_AGENT_EFFORT on models that accept it; `temperature`
    is sent only where the model accepts sampling parameters.
    """
    started = time.monotonic()
    effort = effort if effort is not None else (settings.LLM_AGENT_EFFORT or None)
    first_failure: str | None = None
    result: ChatResult | None = None

    for provider, model, api_key in _candidates("agent"):
        if provider == "none":
            _record(purpose, NOT_CONFIGURED)
            logger.warning("llm.not_configured", purpose=purpose,
                           configured_provider=settings.LLM_PROVIDER, call="chat")
            result = ChatResult(status=NOT_CONFIGURED, provider="none",
                                failure_reason="No API key configured for the selected provider")
        else:
            def call(provider=provider, model=model, api_key=api_key) -> ChatResult:
                if provider == "fake":
                    return _fake._chat(messages=messages, system=system, tools=tools or [],
                                       purpose=purpose)
                if provider == "anthropic":
                    return _chat_anthropic(model, api_key, system, messages, tools or [],
                                           max_tokens, effort, temperature)
                return _chat_openai(provider, model, api_key, system, messages, tools or [],
                                    max_tokens, temperature)

            value, failure = _attempts(purpose, provider, model, call)
            if failure:
                status, detail = failure
                result = ChatResult(status=status, provider=provider, model=model,
                                    failure_reason=detail)
            else:
                result = value
            result.latency_ms = int((time.monotonic() - started) * 1000)
            _record(purpose, result.status)
            log = logger.info if result.status == OK else logger.warning
            log("llm.chat", purpose=purpose, provider=provider, model=model,
                status=result.status, stop_reason=result.stop_reason,
                tool_calls=len(result.tool_calls), input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens, latency_ms=result.latency_ms)
        if result.status not in _FALLBACK_ON:
            break
        first_failure = first_failure or (provider if provider != "none" else settings.LLM_PROVIDER)
    assert result is not None
    if first_failure and result.ai_generated:
        result.fallback_from = first_failure
        logger.warning("llm.fallback_used", purpose=purpose, failed=first_failure,
                       served_by=result.provider, call="chat")
    return result


def _retry_after(exc: Exception) -> float | None:
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        raw = headers.get("retry-after") or headers.get("Retry-After")
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def health() -> dict:
    """Provider, model and whether a key is present — for the health endpoint
    and the startup log. Never includes the key itself."""
    provider, model, api_key = resolved_provider()
    agent_provider, agent_model, _ = resolved_provider("agent")
    fb = (settings.LLM_FALLBACK_PROVIDER or "").strip().lower() or None
    return {
        "configured_provider": settings.LLM_PROVIDER,
        "active_provider": provider,
        "model": model or None,
        "agent_model": agent_model or None,
        "key_present": bool(api_key),
        "usable": provider != "none",
        "fallback_provider": fb,
        "fallback_usable": bool(fb) and _resolve(fb, "fast")[0] != "none",
        "cache_ttl_seconds": settings.LLM_CACHE_TTL_SECONDS,
        "timeout_seconds": settings.LLM_TIMEOUT_SECONDS,
        "counters": stats(),
    }


# ── A scripted provider, for tests ───────────────────────────────────────────
class FakeScriptExhausted(BaseException):
    """A test asked the fake for more turns than it scripted.

    BaseException on purpose: the seam catches Exception and would turn this
    into UPSTREAM_ERROR, which reads as a flaky provider instead of a test bug.
    """


class FakeLLM:
    """Stands in for every provider while installed with use_fake().

    It goes through the real seam — retries, classification, counters, JSON
    parsing, the ChatResult shape — so a test of the agent runtime tests the
    code production runs, minus the network. Script items, consumed in order:

      complete():  str (the text) · dict (JSON data) · Exception (raised, so it
                   is classified and retried like a real SDK error)
      chat():      str (final text) · ToolCall / [ToolCall, ...] (a tool turn) ·
                   ChatResult (returned as given) · Exception

    Or pass a callable(kind, request) -> item. Every request is recorded in
    `.calls` as {"kind", "purpose", ...}.
    """

    model = "fake-model"

    def __init__(self, script: list | Callable[[str, dict], Any] | None = None):
        self._script = script if callable(script) else list(script or [])
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    @staticmethod
    def call(name: str, arguments: dict | None = None, id: str | None = None) -> ToolCall:
        """Shorthand for a scripted tool call."""
        return ToolCall(id=id or f"call_{name}", name=name, arguments=dict(arguments or {}))

    def _next(self, kind: str, request: dict) -> Any:
        self.calls.append({"kind": kind, **request})
        if callable(self._script):
            item = self._script(kind, request)
        else:
            if self._n >= len(self._script):
                raise FakeScriptExhausted(f"FakeLLM script exhausted on call {self._n + 1} ({kind})")
            item = self._script[self._n]
            self._n += 1
        if isinstance(item, BaseException):
            raise item
        return item

    def _complete(self, **request) -> tuple[str, str, str | None]:
        item = self._next("complete", request)
        if isinstance(item, dict):
            return json.dumps(item), "end_turn", None
        return str(item), "end_turn", None

    def _chat(self, **request) -> ChatResult:
        item = self._next("chat", request)
        if isinstance(item, ChatResult):
            return item
        if isinstance(item, ToolCall):
            item = [item]
        if isinstance(item, list):
            calls = list(item)
            return ChatResult(status=OK, tool_calls=calls, stop_reason="tool_use",
                              provider="fake", model=self.model)
        return ChatResult(status=OK, text=str(item), stop_reason="end_turn",
                          provider="fake", model=self.model)


@contextmanager
def use_fake(script: list | Callable[[str, dict], Any] | None = None) -> Iterator[FakeLLM]:
    """Install a FakeLLM for the duration of a with-block."""
    global _fake
    previous = _fake
    fake = script if isinstance(script, FakeLLM) else FakeLLM(script)
    _fake = fake
    try:
        yield fake
    finally:
        _fake = previous
