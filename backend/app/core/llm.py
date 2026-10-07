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
#   - Three new statuses: INVALID_REQUEST (an Anthropic 400 is a parameter the
#     model rejects, not the Groq JSON case), BILLING (402), REFUSED
#     (stop_reason "refusal"). (This read "Four new statuses" over a list of
#     three until the audit of 9204955.)
#   - CHANGED on the groq/openai path, visibly: a reply that hit max_tokens
#     with NO text (finish_reason "length", content "") USED TO return OK with
#     an empty string, so ai_generated read True over nothing — the monthly
#     report endpoint then showed its computed fallback text while reporting
#     "ai_generated": true. It is now BAD_RESPONSE. Every caller gates on
#     ai_generated, so all six take their fallback as for any other failure;
#     four of them also label it (agent.py visit strategy, manager.py briefing /
#     insight / monthly report), while ai_report_service and case_service rank
#     reasons gate without labelling anything.
#
#   Found on the way, and fixed: the openai SDK retries 429/5xx itself
#   (DEFAULT_MAX_RETRIES = 2 in 1.57.4, verified in the fieldops-dev image)
#   UNDER this module's own LLM_MAX_RETRIES = 2. MEASURED against a local
#   server answering 429 to everything, one complete() on groq made 9 HTTP
#   requests while this module logged 3 llm.failed lines — six requests it
#   never saw, never counted, and never applied its Retry-After handling to.
#   Every SDK client is now built with max_retries=0: 3 requests, 3 log lines.
#   The anthropic SDK (1.8.0) has the same default and measured the same, 9
#   before and 3 after. Pinned by test_a_persistent_429_costs_exactly_the_
#   seams_own_attempts, which runs the PRODUCTION constructors' kwargs.
#
# 2026-09-24 — Fix-up after the coordinator's audit of 9204955. Each of these
#   was a real defect in that commit:
#   - OUR bugs read as THEIR outage. Request building and reply parsing ran
#     inside the retry loop's `except Exception`, and anything without a
#     status_code classified as UPSTREAM_ERROR — retryable AND a fallback
#     trigger. A KeyError on a malformed history, ToolCall(**tc) with an
#     unknown key, or the SDK's TypeError on a bad keyword (exactly the
#     `temperature` TypeError found on the way) was retried three times with
#     sleeps and then quietly served by Groq. Now three stages: BUILD (outside
#     the loop; any failure is INVALID_REQUEST — no retry, no fallback), SEND
#     (the only thing retried; a TypeError from the SDK call and a 413 are
#     INVALID_REQUEST), PARSE (outside the loop; failure is BAD_RESPONSE).
#   - A missing SDK read as MODEL_NOT_FOUND ("notfound" matched
#     "modulenotfounderror") on exactly the state the user's un-rebuilt stack
#     is in. Now NOT_CONFIGURED, "SDK not installed", and health() says so.
#   - chat() reported a truncated tool call as runnable: a max_tokens stop
#     mid tool_use returned partial input with wants_tools True. wants_tools
#     now requires stop_reason "tool_use"; any max_tokens stop is BAD_RESPONSE.
#   - LLM_PROVIDER="none" stopped being a kill switch once a fallback was set.
#     An explicit "none" (or any non-provider value) now never falls back.
#   - Fence-stripping of JSON had leaked onto the groq path — a fenced reply
#     that was BAD_RESPONSE had become OK. Now Anthropic-only; groq unchanged.
#     And llm.ok regained the attempt= field it had lost.
#   - chat() gets its own LLM_AGENT_TIMEOUT_SECONDS: the 20 s sized for short
#     prompts would time a 16k-token Sonnet 5 turn out and — TIMEOUT being a
#     fallback trigger — switch provider mid agent loop.
#   - When both providers fail, the PRIMARY's failure is returned, with the
#     fallback's attached as fallback_failure, instead of the fallback's
#     failure hiding the one that mattered.
#   - complete() could raise: json.dumps(json_schema) for the cache key ran
#     outside any guard. A non-serialisable schema is now INVALID_REQUEST.
#   - CHANGED on the groq path: finish_reason "content_filter" in complete()
#     was OK (often over empty text); it is REFUSED, as it already was in chat().
#   - A fallback-served success counts as FALLBACK_OK, not OK, so the counters
#     say which provider answered; health()'s `usable` is true while a fallback
#     serves (primary_usable / unusable_reason say why the primary does not).
#   - use_fake() swaps in an in-process store, so tests run in the api
#     container no longer write counters into the real Redis.
#   - Also true on groq/openai, not just Anthropic — visible here because it
#     was missed the first time round: HTTP 402 is now BILLING and not
#     retried (was UPSTREAM_ERROR, retried up to 3x); HTTP 413 and a
#     TypeError from the SDK call are now INVALID_REQUEST and not retried
#     (were UPSTREAM_ERROR, retried); and a reply the PARSE stage cannot read
#     is now BAD_RESPONSE, not retried (was UPSTREAM_ERROR, retried, and then
#     quietly served by the fallback, since UPSTREAM_ERROR is a fallback
#     trigger). `_classify` and the three-stage split are shared by every
#     provider; the earlier bullets above illustrated them with Anthropic
#     examples only, which read as if groq/openai were unaffected.
#
# 2026-09-24 — Second fix-up, from auditing the fix-up above:
#   - `_settle` still dropped the PRIMARY's failure whenever the fallback
#     failed with a caller-shaped status (BAD_RESPONSE / INVALID_REQUEST /
#     REFUSED) — only a provider-shaped fallback failure got attached to the
#     primary as `fallback_failure`; anything else silently returned the
#     fallback's OWN failure instead, hiding the one that needed fixing. Now:
#     any fallback result that is not `ai_generated` is attached to the
#     primary as `fallback_failure` and the primary is returned; only an
#     `ai_generated` fallback result is ever served.
#   - chat() no longer falls back on TIMEOUT. A 120 s agent turn timing out
#     mid tool loop must not switch provider under it — the loop's state
#     (tool calls already issued against a specific model's tool-calling
#     format) belongs to the provider that started it. `_CHAT_FALLBACK_ON`
#     is `_FALLBACK_ON` minus TIMEOUT, used only by chat()'s own loop.
#     complete() has no loop and is unaffected — it still falls back on
#     TIMEOUT.
#   - Counters: a FALLBACK attempt's status now records under its OWN
#     dimension, `FALLBACK_<STATUS>` — generalising FALLBACK_OK, which
#     already existed for a fallback SUCCESS but let a fallback FAILURE share
#     the primary's counter key, so "the primary is failing" and "the
#     fallback is failing" were indistinguishable in the same count.
#   - `_cache_key`: a lone surrogate in the PROMPT (e.g. "\ud800") raised
#     UnicodeEncodeError from a bare `.encode()`, which the call site caught
#     as "json_schema is not JSON-serialisable" — wrong, and misleading,
#     since the prompt was at fault and there may be no schema at all.
#     `.encode()` now passes `errors="surrogatepass"`, so the prompt can
#     never break key computation. A pathologically deep schema's
#     `json.dumps` can raise RecursionError, which the old guard did not
#     catch; now guarded alongside TypeError/ValueError, and the call site
#     logs a warning whenever it returns INVALID_REQUEST from here.
#   - `_sdk_missing` used `importlib.util.find_spec`, which only proves the
#     package is ON DISK — a package that is present but broken on import (an
#     incompatible dependency, a bad upgrade) read as usable and then failed
#     deeper in the call, misclassified as a provider outage rather than a
#     broken image. It now imports the module and distinguishes "not
#     installed" (ImportError/ModuleNotFoundError) from "installed but failed
#     to import: <ExcType>: <msg>".
#   - main.py's SPA catch-all: a missing hashed asset (`*.js`,
#     case-insensitive) now 404s instead of serving index.html with a 200
#     `text/html` — a stale client or service worker asking for an old chunk
#     (or `/sw.js` after a rollback) got HTML where it expected JavaScript.
#     Not this module; see the CHANGELOG block in app/main.py.
# ───────────────────────────────────────────────────────────────────────────
"""One LLM entry point for the whole product.

Providers, selected via settings.LLM_PROVIDER:
  - "groq"      (default) — OpenAI-compatible, so the same `openai` SDK is used
                            with base_url pointed at Groq. Needs GROQ_API_KEY.
  - "openai"              — the original hosted path. Needs OPENAI_API_KEY.
  - "anthropic"           — Claude, through the `anthropic` SDK. Needs
                            ANTHROPIC_API_KEY.
  - "none"                — never calls out, fallback included; every request
                            reports NOT_CONFIGURED. Useful for demos and tests.

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
import importlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

import structlog

from app.core.config import settings
from app.core.redaction import pseudonymise, restore

logger = structlog.get_logger()

# ── Outcomes ─────────────────────────────────────────────────────────────────
OK = "OK"
CACHED = "CACHED"
NOT_CONFIGURED = "NOT_CONFIGURED"   # no key, no SDK, or provider is "none"
AUTH_FAILED = "AUTH_FAILED"         # 401/403 — wrong or revoked key
BILLING = "BILLING"                 # 402 — the key is fine, the account cannot pay
RATE_LIMITED = "RATE_LIMITED"       # 429 — survived the retries
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"  # 404 — LLM_MODEL is wrong or retired
TIMEOUT = "TIMEOUT"
BAD_RESPONSE = "BAD_RESPONSE"       # answered, but not in a usable shape
INVALID_REQUEST = "INVALID_REQUEST"  # our request was wrong (Anthropic 400, 413, bad kwarg, bad history)
REFUSED = "REFUSED"                 # the model declined (refusal / content_filter)
UPSTREAM_ERROR = "UPSTREAM_ERROR"   # anything else the provider did

# Counter-only label: a success served by LLM_FALLBACK_PROVIDER. The result's
# own status is still OK; the counter says the primary did not answer it.
FALLBACK_OK = "FALLBACK_OK"

# Retrying cannot fix a bad key or an unknown model, and retrying a timeout on a
# request a user is waiting for just doubles the wait.
_RETRYABLE = {RATE_LIMITED, UPSTREAM_ERROR}

# Failures that belong to the PROVIDER, so a different provider might succeed.
# BAD_RESPONSE, INVALID_REQUEST and REFUSED are about the request or the answer;
# sending the same thing elsewhere hides the problem instead of fixing it.
_FALLBACK_ON = {NOT_CONFIGURED, AUTH_FAILED, BILLING, RATE_LIMITED,
                MODEL_NOT_FOUND, TIMEOUT, UPSTREAM_ERROR}

# chat()-only: TIMEOUT never triggers the fallback. A 120 s agent turn timing
# out is not evidence the OTHER provider would do better, and switching mid
# tool loop hands a stateful conversation to a model that did not start it.
# complete() has no loop and keeps TIMEOUT in _FALLBACK_ON.
_CHAT_FALLBACK_ON = _FALLBACK_ON - {TIMEOUT}

_PROVIDERS = ("groq", "openai", "anthropic")
_SDK_MODULE = {"groq": "openai", "openai": "openai", "anthropic": "anthropic"}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def cache_tokens(self) -> int:
        """Read + creation, combined for metering: ai.llm_calls (F11) stores
        one cache count, not two — the split still drives `_cost_usd` at the
        point the row is written, where the row's `cost` column is the
        authority and this combined count is description only."""
        return self.cache_read_input_tokens + self.cache_creation_input_tokens


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
    # Set when this result came from LLM_FALLBACK_PROVIDER: the provider that
    # failed first, so "the AI works" is never mistaken for "the primary works".
    fallback_from: str | None = None
    # Set on the PRIMARY's failure when the fallback was tried and failed too:
    # {"provider", "status", "failure_reason"} of the fallback's attempt.
    fallback_failure: dict[str, Any] | None = None
    #: How many identifiers were replaced before the send, by kind. Evidence of
    #: what left the building; never the values themselves.
    redactions: dict[str, int] = field(default_factory=dict)
    # 2026-10-07 (F11): complete() never read `value.usage` before this — only
    # chat() did. Metering needs it on the path that actually carries nearly
    # all production traffic, so it is captured here too, zero by default
    # (no response reached parse(), nothing was billed).
    usage: Usage = field(default_factory=Usage)

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
    fallback_failure: dict[str, Any] | None = None
    # The provider's own content blocks, as plain dicts. Anthropic requires
    # thinking blocks to be sent back unchanged on the next turn of a tool loop;
    # a text + tool_calls reconstruction would silently drop them.
    provider_content: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ai_generated(self) -> bool:
        return self.status == OK

    @property
    def wants_tools(self) -> bool:
        """The model finished a turn by asking for tools. A max_tokens stop is
        never this, even with tool_use blocks present — their input may be cut
        off, and running a tool on a truncated argument is worse than failing."""
        return self.status == OK and self.stop_reason == "tool_use" and bool(self.tool_calls)

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


def _norm(name: str | None) -> str:
    return (name or "none").strip().lower()


def _sdk_missing(name: str) -> str | None:
    """Why this provider's SDK cannot be used, or None when it can.

    Actually IMPORTS the module rather than asking find_spec for one: a spec
    only proves the package is on disk, so a package that is present but
    broken on import (an incompatible dependency, a syntax error from a bad
    upgrade) used to read as usable and fail later, deeper in the call —
    misclassified as a provider outage instead of a broken image. Two
    distinct reasons now, so a reader is told which one they are looking at.
    """
    mod = _SDK_MODULE.get(name)
    if mod is None:
        return None
    try:
        importlib.import_module(mod)
    except ImportError:
        return (f"the {mod} SDK is not installed in this image — rebuild it from "
                f"backend/requirements.txt")
    except Exception as exc:
        return f"the {mod} SDK is installed but failed to import: {type(exc).__name__}: {exc}"
    return None


def _resolve(name: str, tier: str) -> tuple[str, str, str]:
    """(provider, model, api_key) for one named provider. "none" when unusable."""
    p = _norm(name)
    if p == "groq":
        model, key = settings.LLM_MODEL, settings.GROQ_API_KEY
    elif p == "openai":
        model, key = settings.LLM_MODEL_OPENAI, settings.OPENAI_API_KEY
    elif p == "anthropic":
        model = (settings.LLM_MODEL_ANTHROPIC_AGENT if tier == "agent"
                 else settings.LLM_MODEL_ANTHROPIC)
        key = settings.ANTHROPIC_API_KEY
    else:
        return ("none", "", "")
    usable = bool(key) and _sdk_missing(p) is None
    return (p if usable else "none", model, key)


def _unusable_reason(name: str) -> str:
    p = _norm(name)
    if p not in _PROVIDERS:
        return f"LLM_PROVIDER is {name!r}: outbound LLM calls are switched off"
    return _sdk_missing(p) or "No API key configured for the selected provider"


def resolved_provider(tier: str = "fast") -> tuple[str, str, str]:
    """(provider, model, api_key) for the configured primary provider.

    `tier` is "fast" (complete) or "agent" (chat). Only Anthropic has two
    models; groq and openai answer both tiers with their one configured model.
    """
    if _fake is not None:
        return ("fake", _fake.model, "fake")
    return _resolve(settings.LLM_PROVIDER, tier)


def _fallback_name() -> str | None:
    """The fallback provider, or None. None whenever the primary is not a real
    provider: an explicit "none" is a kill switch, and a kill switch that a
    fallback quietly routes around is not one."""
    primary, fb = _norm(settings.LLM_PROVIDER), _norm(settings.LLM_FALLBACK_PROVIDER)
    if _fake is not None or primary not in _PROVIDERS or fb not in _PROVIDERS or fb == primary:
        return None
    return fb


def _candidates(tier: str) -> list[tuple[str, str, str, str]]:
    """[(configured name, provider, model, key)]: the primary, then the fallback."""
    if _fake is not None:
        return [("fake", "fake", _fake.model, "fake")]
    primary = _norm(settings.LLM_PROVIDER)
    out = [(primary, *_resolve(primary, tier))]
    fb = _fallback_name()
    if fb:
        out.append((fb, *_resolve(fb, tier)))
    return out


def _client(provider: str, api_key: str, timeout: float | None = None):
    from openai import OpenAI
    t = settings.LLM_TIMEOUT_SECONDS if timeout is None else timeout
    # max_retries=0: this module owns retries (see the 2026-09-24 note above).
    if provider == "groq":
        # Groq speaks the OpenAI wire protocol, so no new dependency is needed —
        # only a different base_url.
        return OpenAI(api_key=api_key, base_url=settings.GROQ_BASE_URL,
                      timeout=t, max_retries=0)
    return OpenAI(api_key=api_key, timeout=t, max_retries=0)


def _anthropic_client(api_key: str, timeout: float | None = None):
    import anthropic
    t = settings.LLM_TIMEOUT_SECONDS if timeout is None else timeout
    return anthropic.Anthropic(api_key=api_key, timeout=t, max_retries=0)


def _classify(exc: Exception, provider: str = "") -> str:
    """Map an exception raised by the SDK CALL onto one of our statuses.

    Only the send stage reaches this: request building and reply parsing run
    outside the retry loop and are classified where they happen. Matched on
    status_code first and class name second, rather than by importing the
    SDK's exception classes, so a minor SDK version bump cannot silently turn
    every failure into UPSTREAM_ERROR. Works for the openai and anthropic SDKs
    alike: both carry `status_code` and `response` on their status errors.
    """
    if isinstance(exc, ImportError):
        # Before any name matching: "ModuleNotFoundError" contains "notfound".
        return NOT_CONFIGURED
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
    if code == 413:
        return INVALID_REQUEST      # request too large: our history, not their outage
    if code == 429:
        return RATE_LIMITED
    if isinstance(exc, TypeError):
        # The SDK rejected a keyword before sending: our request shape. This is
        # how anthropic 1.x reports `temperature`.
        return INVALID_REQUEST
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


def _refusal_reason(resp: Any) -> str:
    det = getattr(resp, "stop_details", None)
    return f"refused ({getattr(det, 'category', None) or 'unspecified'})"


# ── Usage metering (F11) ──────────────────────────────────────────────────────
# Each provider's own published list price, USD per 1,000,000 tokens, as of
# 2026-10. Not fetched from any billing API — there is none wired up here — so
# this is a hand-maintained constant, same discipline as the scorecards: wrong
# until someone updates it, never silently re-estimated per call. A model
# absent from the table prices as None rather than a guessed number (ADR
# 0005's "abstain rather than impute", borrowed for cost).
#
# cache_creation (a cache WRITE) is priced like input here rather than at its
# own, typically higher, per-provider surcharge rate: none of today's callers
# set a cache_ttl long enough to make a write-then-reuse worthwhile, so a
# creation event is rare, and folding it into the input rate is a safe
# over-simplification to flag rather than a second table to get wrong.
_PRICE_PER_MTOK_USD: dict[str, tuple[float, float, float]] = {
    # model: (input, output, cache_read)
    "claude-haiku-4-5-20251001": (1.00, 5.00, 0.10),
    "claude-sonnet-5": (3.00, 15.00, 0.30),
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "openai/gpt-oss-120b": (0.15, 0.75, 0.0),
}


def _cost_usd(model: str, usage: Usage) -> float | None:
    """None, never 0.0, when `model` is not in the table — a real zero (a
    known model, no tokens) must stay distinguishable from "price unknown"."""
    prices = _PRICE_PER_MTOK_USD.get(model)
    if prices is None:
        return None
    in_rate, out_rate, cache_read_rate = prices
    return (
        usage.input_tokens * in_rate
        + usage.output_tokens * out_rate
        + usage.cache_read_input_tokens * cache_read_rate
        + usage.cache_creation_input_tokens * in_rate
    ) / 1_000_000


def _record_usage(purpose: str, bank_id: str | None, provider: str, model: str,
                  usage: Usage) -> None:
    """One row in ai.llm_calls per complete()/chat() call. Best-effort, like
    every other write at this seam: a metering row is never the reason a
    product feature fails, so any failure here is caught and logged, not
    raised.

    Skipped for "fake"/"none": a scripted test provider and a disabled one
    never spend real money, and skipping them here means the hundreds of
    LLM-seam unit tests that run with no database configured never attempt
    one — `use_fake()` makes this unconditional rather than best-effort.

    `bank_id` is whatever the caller passed (default None). None of today's
    six call sites pass one yet — the same "known gap, counted not hidden"
    shape as AuditLog's unattributed rows (services/bank/audit_read.py):
    GET /bank/usage counts calls with no bank rather than guessing one.
    """
    if provider in ("fake", "none") or _fake is not None:
        return
    try:
        from app.core.database import SessionLocal
        from app.models.llm_call import LLMCall
        db = SessionLocal()
        try:
            db.add(LLMCall(
                bank_id=bank_id, provider=provider, model=model, feature=purpose,
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                cache_tokens=usage.cache_tokens, cost=_cost_usd(model, usage),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as exc:                                      # noqa: BLE001 — never raise
        logger.warning("llm.usage_record_failed", purpose=purpose, model=model,
                       error=str(exc), error_type=type(exc).__name__)


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
        # when absent so every pre-existing key is unchanged. May raise
        # TypeError/ValueError (not JSON-serialisable) or RecursionError (a
        # pathologically deep schema) — the caller decides what that means.
        raw += "\x00" + json.dumps(schema, sort_keys=True)
    # errors="surrogatepass": a lone surrogate in the PROMPT (never the
    # schema) raises UnicodeEncodeError from a bare .encode(), which used to
    # be caught by the caller and misreported as a schema problem. The key is
    # only ever hashed, never decoded, so a lossy-but-non-raising encoding of
    # a surrogate is harmless here.
    digest = hashlib.sha256(raw.encode(errors="surrogatepass")).hexdigest()[:32]
    return f"llm:cache:{purpose}:{digest}"


def _fallback_key(status: str, is_fallback: bool) -> str:
    """The counter key for one provider attempt. A FALLBACK attempt's status
    is recorded under its own dimension (FALLBACK_<STATUS>) rather than the
    primary's — FALLBACK_OK already existed for a fallback SUCCESS; this
    generalises it so a fallback FAILURE cannot be mistaken for the primary's
    own failure rate in the same counter."""
    return f"FALLBACK_{status}" if is_fallback else status


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


# ── The three stages: build, send (retried), parse ───────────────────────────
def _attempts(purpose: str, provider: str, model: str, send: Callable[[], Any]):
    """Run the SEND stage with the retry policy.

    Returns (value, None, attempt) or (None, (status, detail), attempt).
    Only the SDK call is in here — building and parsing are not, so a bug in
    our own code can never be retried or mistaken for a provider outage.
    """
    last_status, last_detail, attempt = UPSTREAM_ERROR, None, 0
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            return send(), None, attempt
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
    return None, (last_status, last_detail), attempt


def _build_failed(purpose: str, provider: str, model: str, exc: Exception) -> tuple[str, str]:
    """Classify a failure of the BUILD stage (never retried, never a fallback
    trigger unless the SDK itself is missing)."""
    if isinstance(exc, ImportError):
        reason = (f"the {_SDK_MODULE.get(provider, provider)} SDK could not be imported "
                  f"({exc}) — rebuild the image from backend/requirements.txt")
        logger.warning("llm.not_configured", purpose=purpose, provider=provider, reason=reason)
        return NOT_CONFIGURED, reason
    reason = f"could not build the request: {type(exc).__name__}: {exc}"[:300]
    # ERROR with a traceback: this is a bug on our side, not a provider event.
    logger.error("llm.invalid_request", purpose=purpose, provider=provider, model=model,
                 error=reason, exc_info=exc)
    return INVALID_REQUEST, reason


def _parse_failed(purpose: str, provider: str, model: str, exc: Exception) -> str:
    reason = f"could not read the provider's reply: {type(exc).__name__}: {exc}"[:300]
    logger.error("llm.unreadable_response", purpose=purpose, provider=provider, model=model,
                 error=reason, exc_info=exc)
    return reason


def _settle(purpose: str, tried: list[tuple[str, Any]], call: str):
    """Pick what the caller sees from the primary's and (maybe) the fallback's
    result.

    Only an `ai_generated` fallback result is ever served. Anything else —
    a provider-shaped failure (RATE_LIMITED, NOT_CONFIGURED, ...) or a
    caller-shaped one (BAD_RESPONSE, INVALID_REQUEST, REFUSED) — means the
    fallback did not actually answer, so the PRIMARY's result is returned
    with the fallback's attempt attached as `fallback_failure`. Deciding this
    on `ai_generated` rather than membership in _FALLBACK_ON is deliberate:
    the earlier version returned the fallback's own caller-shaped failure
    (e.g. its BAD_RESPONSE) as if it had served the request, silently
    dropping the primary's failure — the one that actually needed fixing.
    """
    primary_name, primary = tried[0]
    if len(tried) == 1:
        return primary
    fb_name, fb = tried[-1]
    if fb.ai_generated:
        fb.fallback_from = primary_name
        logger.warning("llm.fallback_used", purpose=purpose, call=call, failed=primary_name,
                       primary_status=primary.status, served_by=fb_name, status=fb.status)
        return fb
    primary.fallback_failure = {"provider": fb_name, "status": fb.status,
                                "failure_reason": fb.failure_reason}
    logger.warning("llm.fallback_failed", purpose=purpose, call=call,
                   primary=primary_name, primary_status=primary.status,
                   fallback=fb_name, fallback_status=fb.status)
    return primary


# ── complete(): one-shot text or JSON ────────────────────────────────────────
def _openai_complete_kwargs(model, prompt, system, want_json, json_schema, max_tokens,
                            temperature) -> dict[str, Any]:
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
    if want_json:
        kwargs["response_format"] = {"type": "json_object"}
    if settings.LLM_REASONING_EFFORT:
        # Not a typed parameter on openai==1.57.4, so it rides in extra_body.
        # A provider that does not recognise it ignores it.
        kwargs["extra_body"] = {"reasoning_effort": settings.LLM_REASONING_EFFORT}
    return kwargs


def _anthropic_complete_kwargs(model, prompt, system, want_json, json_schema, max_tokens,
                               temperature) -> dict[str, Any]:
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
    elif want_json:
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
    return kwargs


def _parse_openai_complete(resp: Any) -> tuple[str, str, str | None]:
    choice = resp.choices[0]
    stop = _stop(getattr(choice, "finish_reason", None))
    return ((choice.message.content or "").strip(), stop,
            "refused (content_filter)" if stop == "refusal" else None)


def _parse_anthropic_complete(resp: Any) -> tuple[str, str, str | None]:
    text = "".join(getattr(b, "text", "") or "" for b in resp.content
                   if getattr(b, "type", None) == "text").strip()
    stop = _stop(getattr(resp, "stop_reason", None))
    return text, stop, (_refusal_reason(resp) if stop == "refusal" else None)


def _build_complete(provider, model, api_key, prompt, *, purpose, system, want_json,
                    json_schema, max_tokens, temperature):
    """BUILD stage: returns (send, parse). Anything raised here is our bug (or a
    missing SDK) and is classified by _build_failed, never retried."""
    if provider == "fake":
        def send():
            return _fake._complete(prompt=prompt, system=system, purpose=purpose,
                                   json_mode=want_json, json_schema=json_schema)
        return send, (lambda v: v)
    if provider == "anthropic":
        client = _anthropic_client(api_key)
        kwargs = _anthropic_complete_kwargs(model, prompt, system, want_json, json_schema,
                                            max_tokens, temperature)
        return (lambda: client.messages.create(**kwargs)), _parse_anthropic_complete
    client = _client(provider, api_key)
    kwargs = _openai_complete_kwargs(model, prompt, system, want_json, json_schema,
                                     max_tokens, temperature)
    return (lambda: client.chat.completions.create(**kwargs)), _parse_openai_complete


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
    names: Iterable[str] = (),
    redact_values: Iterable[str] = (),
    bank_id: str | None = None,
) -> LLMResult:
    """Ask the model. Returns an LLMResult; never raises.

    `purpose` is a short stable label for the feature making the call
    ("visit_strategy", "briefing", ...). It keys the logs, the counters and the
    cache, and is what turns a vague "the AI is down" into a specific answer.

    `json_schema` (optional) implies json_mode. On Anthropic it is enforced by
    the API (structured outputs); on OpenAI-compatible providers it is given to
    the model and the reply is still parsed and checked for an object.

    `bank_id` (optional, F11): attributes the call's usage/cost row to a bank.
    Omitted it is recorded unattributed, same as an audit row with no tenant.
    """
    started = time.monotonic()
    want_json = json_mode or json_schema is not None
    # No identifier crosses this seam. Pseudonymised rather than blanked, so
    # the answer can be turned back into something an agent can read, and done
    # HERE so no caller can forget it — on the fallback's path too.
    try:
        red = pseudonymise(prompt, system, names=names, values=redact_values)
    except Exception as exc:                                    # noqa: BLE001 — fail closed
        logger.error("llm.redaction_failed", purpose=purpose, error=str(exc),
                     error_type=type(exc).__name__, exc_info=True)
        return LLMResult(status=INVALID_REQUEST,
                         failure_reason="redaction failed; the request was not sent")
    if red.changed:
        # Counts only: what was removed, never what it was.
        logger.info("llm.redacted", purpose=purpose, counts=red.counts)

    tried: list[tuple[str, LLMResult]] = []
    for i, (name, provider, model, api_key) in enumerate(_candidates("fast")):
        r = _complete_one(name, provider, model, api_key, red.prompt, purpose=purpose,
                          system=red.system, want_json=want_json, json_schema=json_schema,
                          max_tokens=max_tokens, temperature=temperature,
                          cache_ttl=cache_ttl, started=started, is_fallback=i > 0,
                          key_prompt=prompt, key_system=system)
        tried.append((name, r))
        if r.status not in _FALLBACK_ON:
            break
    result = _settle(purpose, tried, "complete")
    result.redactions = dict(red.counts)
    if red.changed:
        result.text = restore(result.text, red.mapping)
        result.data = restore(result.data, red.mapping)
    _record_usage(purpose, bank_id, result.provider, result.model, result.usage)
    return result


def _complete_one(name, provider, model, api_key, prompt, *, purpose, system, want_json,
                  json_schema, max_tokens, temperature, cache_ttl, started,
                  is_fallback, key_prompt=None, key_system=None) -> LLMResult:
    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    def fail(status: str, reason: str | None, **extra) -> LLMResult:
        _record(purpose, _fallback_key(status, is_fallback))
        return LLMResult(status=status, provider=provider, model=model,
                         failure_reason=reason, latency_ms=elapsed(), **extra)

    if provider == "none":
        reason = _unusable_reason(name)
        logger.warning("llm.not_configured", purpose=purpose, configured_provider=name,
                       reason=reason)
        _record(purpose, _fallback_key(NOT_CONFIGURED, is_fallback))
        return LLMResult(status=NOT_CONFIGURED, provider="none", failure_reason=reason)

    try:
        # Keyed on what the CALLER asked, not on the pseudonymised send:
        # two borrowers' prompts differ only in the values just replaced, and
        # a shared key would serve one borrower's brief for another.
        key = _cache_key(purpose, model,
                         prompt if key_prompt is None else key_prompt,
                         system if key_system is None else key_system, json_schema)
    except (TypeError, ValueError, RecursionError) as exc:
        # The prompt itself can no longer reach here (see _cache_key) — only
        # json.dumps(json_schema) can still raise, so this reason is accurate.
        reason = f"json_schema is not JSON-serialisable: {exc}"
        logger.warning("llm.invalid_request", purpose=purpose, provider=provider,
                       model=model, error=reason)
        return fail(INVALID_REQUEST, reason)

    ttl = settings.LLM_CACHE_TTL_SECONDS if cache_ttl is None else cache_ttl
    if ttl > 0:
        try:
            hit = _get_store().get(key)
        except Exception:
            hit = None
        if hit:
            payload = json.loads(hit)
            _record(purpose, _fallback_key(CACHED, is_fallback))
            return LLMResult(
                status=CACHED, text=payload.get("text", ""), data=payload.get("data") or {},
                provider=provider, model=model, cached=True, latency_ms=elapsed(),
            )

    try:
        send, parse = _build_complete(provider, model, api_key, prompt, purpose=purpose,
                                      system=system, want_json=want_json,
                                      json_schema=json_schema, max_tokens=max_tokens,
                                      temperature=temperature)
    except Exception as exc:
        return fail(*_build_failed(purpose, provider, model, exc))

    value, failure, attempt = _attempts(purpose, provider, model, send)
    if failure:
        return fail(*failure)

    try:
        text, stop, refusal = parse(value)
    except Exception as exc:
        return fail(BAD_RESPONSE, _parse_failed(purpose, provider, model, exc))

    # From here on a real response was received and is billed by the provider
    # whether or not it turns out usable below — captured once, attached to
    # every return path past this point (F11 metering).
    usage = _usage_from(getattr(value, "usage", None))

    if stop == "refusal":
        logger.warning("llm.refused", purpose=purpose, provider=provider, model=model,
                       reason=refusal)
        return fail(REFUSED, refusal or "refused", text=text, stop_reason=stop, usage=usage)
    if stop == "max_tokens" and not text:
        # The whole budget went on reasoning and no answer was started — the
        # gpt-oss failure LLM_REASONING_EFFORT exists for, seen from this side.
        logger.warning("llm.no_answer_in_budget", purpose=purpose, provider=provider,
                       model=model)
        return fail(BAD_RESPONSE, "Output budget ran out before an answer was written",
                    stop_reason=stop, usage=usage)

    data: dict[str, Any] = {}
    if want_json:
        # Fences are stripped for Anthropic only: without a native json_object
        # mode a Claude reply may arrive fenced. The groq path keeps its
        # pre-F01 contract — a fenced reply there is still BAD_RESPONSE.
        raw = _strip_fences(text) if provider == "anthropic" else text
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            # The model answered but not in the shape we asked for. A
            # weaker model drifting on a strict JSON contract lands here,
            # which is exactly what we want visible rather than swallowed.
            #
            # 2026-09-28 — this used to log preview=text[:120]: the model's raw
            # output, which can echo borrower PII straight out of the prompt
            # (name, phone, address, amounts) into the log line. text_length
            # and a hash go out instead of the text itself.
            #
            # error=str(exc) had the same problem one layer down: `exc` is
            # whatever json.loads(raw) raised, always a json.JSONDecodeError
            # today, whose message is positional only (line/column/char) and
            # never embeds the document — but the except clause catches the
            # wider (ValueError, TypeError), and a future change to this
            # branch (a pydantic validator, say) could raise something whose
            # str() DOES embed its input, and str(exc) would carry it into the
            # log unnoticed. error_type is logged for every exception; the
            # message itself only when the type is provably safe.
            error_detail: dict[str, Any] = {"error_type": type(exc).__name__}
            if isinstance(exc, json.JSONDecodeError):
                error_detail.update(error_msg=exc.msg, error_line=exc.lineno,
                                    error_col=exc.colno)
            logger.warning("llm.bad_response", purpose=purpose, provider=provider,
                           model=model, text_length=len(text),
                           text_sha256=hashlib.sha256(text.encode(errors="surrogatepass")).hexdigest()[:16],
                           **error_detail)
            return fail(BAD_RESPONSE, "Model did not return valid JSON", text=text,
                        stop_reason=stop, usage=usage)
        data = parsed if isinstance(parsed, dict) else {"value": parsed}

    if ttl > 0:
        try:
            _get_store().setex(key, ttl, json.dumps({"text": text, "data": data}))
        except Exception:
            pass
    _record(purpose, _fallback_key(OK, is_fallback))
    logger.info("llm.ok", purpose=purpose, provider=provider, model=model,
                latency_ms=elapsed(), attempt=attempt, stop_reason=stop, cached=False,
                fallback=is_fallback)
    return LLMResult(status=OK, text=text, data=data, provider=provider,
                     model=model, stop_reason=stop, latency_ms=elapsed(), usage=usage)


# ── chat(): one tool-calling turn ────────────────────────────────────────────
def _as_call(tc: ToolCall | dict) -> ToolCall:
    return tc if isinstance(tc, ToolCall) else ToolCall(**tc)


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
        role = m["role"]
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
            for tc in (_as_call(t) for t in m.get("tool_calls") or []):
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name,
                               "input": tc.arguments})
            out.append({"role": "assistant", "content": blocks or ""})
        elif role == "user":
            out.append({"role": "user", "content": m.get("content", "")})
        else:
            raise ValueError(f"unknown message role {role!r}")
    flush()
    return out


def _to_openai(system: str | None, messages: list[dict]) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    for m in messages:
        role = m["role"]
        if role == "tool":
            content = str(m.get("content", ""))
            # No is_error field on this wire format, so the model is told in text.
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                        "content": ("ERROR: " + content) if m.get("is_error") else content})
        elif role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
            calls = [_as_call(tc) for tc in (m.get("tool_calls") or [])]
            if calls:
                msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                      "function": {"name": tc.name,
                                                   "arguments": json.dumps(tc.arguments)}}
                                     for tc in calls]
            out.append(msg)
        elif role == "user":
            out.append({"role": "user", "content": m.get("content", "")})
        else:
            raise ValueError(f"unknown message role {role!r}")
    return out


def _parse_anthropic_chat(model: str):
    def parse(resp: Any) -> ChatResult:
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
            res.status, res.failure_reason = REFUSED, _refusal_reason(resp)
        return res
    return parse


def _parse_openai_chat(provider: str, model: str):
    def parse(resp: Any) -> ChatResult:
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
        if calls and stop == "end_turn":
            # Some OpenAI-compatible servers finish a tool turn with "stop".
            # The calls are complete either way; only "length" truncates them.
            stop = "tool_use"
        res = ChatResult(status=OK, text=(msg.content or "").strip(), tool_calls=calls,
                         stop_reason=stop, provider=provider, model=model,
                         usage=_usage_from(getattr(resp, "usage", None)))
        if stop == "refusal":
            res.status, res.failure_reason = REFUSED, "refused (content_filter)"
        return res
    return parse


def _build_chat(provider, model, api_key, *, purpose, system, messages, tools, max_tokens,
                effort, temperature):
    """BUILD stage for chat(): translate history + tools, construct the client."""
    if provider == "fake":
        def send():
            return _fake._chat(messages=messages, system=system, tools=tools, purpose=purpose)
        return send, (lambda v: v)

    timeout = settings.LLM_AGENT_TIMEOUT_SECONDS
    if provider == "anthropic":
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
        client = _anthropic_client(api_key, timeout)
        return (lambda: client.messages.create(**kwargs)), _parse_anthropic_chat(model)

    kwargs = {"model": model, "messages": _to_openai(system, messages),
              "max_tokens": max_tokens}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = [{"type": "function",
                            "function": {"name": t.name, "description": t.description,
                                         "parameters": t.parameters}} for t in tools]
    if settings.LLM_REASONING_EFFORT:
        kwargs["extra_body"] = {"reasoning_effort": settings.LLM_REASONING_EFFORT}
    client = _client(provider, api_key, timeout)
    return (lambda: client.chat.completions.create(**kwargs)), _parse_openai_chat(provider, model)


def chat(
    messages: list[dict],
    *,
    purpose: str,
    system: str | None = None,
    tools: list[ToolSpec] | None = None,
    max_tokens: int = 16000,
    effort: str | None = None,
    temperature: float | None = None,
    bank_id: str | None = None,
) -> ChatResult:
    """One model turn with tools. Returns a ChatResult; never raises.

    The caller runs the loop: execute `result.tool_calls`, append
    `result.assistant_message()` and one `tool_result(...)` per call, and call
    chat() again while `result.wants_tools`. Nothing is cached.

    `effort` overrides LLM_AGENT_EFFORT on models that accept it; `temperature`
    is sent only where the model accepts sampling parameters. A max_tokens stop
    is BAD_RESPONSE: the turn was cut off, and a truncated tool call must not run.

    `bank_id` (optional, F11): attributes the call's usage/cost row to a bank,
    same as on complete().
    """
    started = time.monotonic()
    effort = effort if effort is not None else (settings.LLM_AGENT_EFFORT or None)
    tried: list[tuple[str, ChatResult]] = []
    for i, (name, provider, model, api_key) in enumerate(_candidates("agent")):
        r = _chat_one(name, provider, model, api_key, purpose=purpose, system=system,
                      messages=messages, tools=tools or [], max_tokens=max_tokens,
                      effort=effort, temperature=temperature, started=started,
                      is_fallback=i > 0)
        tried.append((name, r))
        if r.status not in _CHAT_FALLBACK_ON:
            break
    result = _settle(purpose, tried, "chat")
    _record_usage(purpose, bank_id, result.provider, result.model, result.usage)
    return result


def _chat_one(name, provider, model, api_key, *, purpose, system, messages, tools,
              max_tokens, effort, temperature, started, is_fallback) -> ChatResult:
    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    if provider == "none":
        reason = _unusable_reason(name)
        logger.warning("llm.not_configured", purpose=purpose, configured_provider=name,
                       reason=reason, call="chat")
        _record(purpose, _fallback_key(NOT_CONFIGURED, is_fallback))
        return ChatResult(status=NOT_CONFIGURED, provider="none", failure_reason=reason)

    attempt = 0
    try:
        send, parse = _build_chat(provider, model, api_key, purpose=purpose, system=system,
                                  messages=messages, tools=tools, max_tokens=max_tokens,
                                  effort=effort, temperature=temperature)
    except Exception as exc:
        status, reason = _build_failed(purpose, provider, model, exc)
        result = ChatResult(status=status, provider=provider, model=model,
                            failure_reason=reason)
    else:
        value, failure, attempt = _attempts(purpose, provider, model, send)
        if failure:
            result = ChatResult(status=failure[0], provider=provider, model=model,
                                failure_reason=failure[1])
        else:
            try:
                result = parse(value)
            except Exception as exc:
                result = ChatResult(status=BAD_RESPONSE, provider=provider, model=model,
                                    failure_reason=_parse_failed(purpose, provider, model, exc))
            if result.status == OK and result.stop_reason == "max_tokens":
                result.status = BAD_RESPONSE
                result.failure_reason = ("Output budget ran out mid-turn — an answer or a "
                                         "tool call was cut off")

    result.latency_ms = elapsed()
    _record(purpose, _fallback_key(result.status, is_fallback))
    log = logger.info if result.status == OK else logger.warning
    log("llm.chat", purpose=purpose, provider=provider, model=model, status=result.status,
        stop_reason=result.stop_reason, tool_calls=len(result.tool_calls), attempt=attempt,
        input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
        latency_ms=result.latency_ms, fallback=is_fallback)
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
    _, agent_model, _ = resolved_provider("agent")
    fb = _fallback_name()
    fb_usable = bool(fb) and _resolve(fb, "fast")[0] != "none"
    primary_usable = provider != "none"
    return {
        "configured_provider": settings.LLM_PROVIDER,
        "active_provider": provider,
        "model": model or None,
        "agent_model": agent_model or None,
        "key_present": bool(api_key),
        # Can a call succeed at all — through the primary or the fallback.
        "usable": primary_usable or fb_usable,
        "primary_usable": primary_usable,
        "unusable_reason": None if primary_usable else _unusable_reason(settings.LLM_PROVIDER),
        "fallback_provider": fb,
        "fallback_usable": fb_usable,
        "cache_ttl_seconds": settings.LLM_CACHE_TTL_SECONDS,
        "timeout_seconds": settings.LLM_TIMEOUT_SECONDS,
        "agent_timeout_seconds": settings.LLM_AGENT_TIMEOUT_SECONDS,
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
            return ChatResult(status=OK, tool_calls=list(item), stop_reason="tool_use",
                              provider="fake", model=self.model)
        return ChatResult(status=OK, text=str(item), stop_reason="end_turn",
                          provider="fake", model=self.model)


@contextmanager
def use_fake(script: list | Callable[[str, dict], Any] | None = None) -> Iterator[FakeLLM]:
    """Install a FakeLLM for the duration of a with-block.

    Also swaps in a fresh in-process store, so a test's counters and cache
    never reach a real Redis (the api container has one).
    """
    global _fake, _store
    previous_fake, previous_store = _fake, _store
    fake = script if isinstance(script, FakeLLM) else FakeLLM(script)
    _fake, _store = fake, _MemoryStore()
    try:
        yield fake
    finally:
        _fake, _store = previous_fake, previous_store
