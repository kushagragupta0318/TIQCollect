# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-24 (F01). Covers what core/llm.py gained beside complete():
# the Anthropic provider, chat() tool turns for both wire formats, the fallback
# provider, FakeLLM, and the max_retries=0 fix. tests/test_llm.py is untouched
# and still pins the groq/openai behaviour every existing caller relies on.
#
# Two layers. Most tests stub the SDK client so each branch is cheap to reach.
# The last section runs the REAL anthropic and openai SDKs against an in-process
# mock HTTP transport, because a stub cannot tell us that a parameter name is
# wrong — the SDK's own serialisation and response parsing can. No network, no
# key, no database.
#
# 2026-09-24, audit fix-up: the real-SDK tests used to inject clients already
# built with max_retries=0, so they would have passed had production dropped
# it. They now WRAP the SDK constructors — production's own kwargs reach the
# real SDK, only the transport is swapped — and the 9 -> 3 retry measurement
# is an executable test. The last section pins each audit finding.
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from app.core import llm
from app.core.config import settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(settings, "LLM_MODEL_ANTHROPIC", "claude-haiku-4-5")
    monkeypatch.setattr(settings, "LLM_MODEL_ANTHROPIC_AGENT", "claude-sonnet-5")
    monkeypatch.setattr(settings, "LLM_AGENT_EFFORT", "medium")
    monkeypatch.setattr(settings, "LLM_REASONING_EFFORT", "low")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "groq-model")
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "LLM_CACHE_TTL_SECONDS", 60)
    monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 20.0)
    monkeypatch.setattr(settings, "LLM_AGENT_TIMEOUT_SECONDS", 120.0)
    monkeypatch.setattr(llm, "_store", llm._MemoryStore())
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    monkeypatch.setattr(llm, "_fake", None)
    yield
    llm._store = None


# ── stub SDK clients ─────────────────────────────────────────────────────────
def _text(t):
    return NS(type="text", text=t)


def _tool_use(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def _amsg(blocks, stop="end_turn", usage=None, stop_details=None):
    return NS(content=blocks, stop_reason=stop, stop_details=stop_details,
              usage=usage or NS(input_tokens=11, output_tokens=7,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0))


class _AnthropicStub:
    def __init__(self, behaviour):
        self._b = behaviour
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self._b(kwargs, len(self.calls)) if callable(self._b) else self._b
        if isinstance(out, Exception):
            raise out
        return out


def _anthropic(monkeypatch, behaviour):
    stub = _AnthropicStub(behaviour)
    monkeypatch.setattr(llm, "_anthropic_client", lambda *_a, **_k: stub)
    return stub


class _OpenAIStub:
    def __init__(self, behaviour):
        self._b = behaviour
        self.calls: list[dict] = []
        self.chat = NS(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self._b(kwargs, len(self.calls)) if callable(self._b) else self._b
        if isinstance(out, Exception):
            raise out
        return out


def _oresp(content=None, tool_calls=None, finish="stop"):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls),
                          finish_reason=finish)],
              usage=NS(prompt_tokens=5, completion_tokens=3))


def _openai(monkeypatch, behaviour):
    stub = _OpenAIStub(behaviour)
    monkeypatch.setattr(llm, "_client", lambda *_a, **_k: stub)
    return stub


def _http_err(code):
    exc = Exception(f"simulated {code}")
    exc.response = NS(status_code=code, headers={})
    return exc


# ── provider resolution ──────────────────────────────────────────────────────
def test_anthropic_has_a_fast_and_an_agent_model():
    assert llm.resolved_provider() == ("anthropic", "claude-haiku-4-5", "sk-ant-test")
    assert llm.resolved_provider("agent") == ("anthropic", "claude-sonnet-5", "sk-ant-test")


def test_groq_answers_both_tiers_with_its_one_model(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    assert llm.resolved_provider("fast")[1] == llm.resolved_provider("agent")[1] == "groq-model"


def test_anthropic_without_a_key_is_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    r = llm.complete("p", purpose="x")
    assert r.status == llm.NOT_CONFIGURED and r.ai_generated is False


# ── complete() on Anthropic ──────────────────────────────────────────────────
def test_anthropic_plain_completion_joins_text_blocks(monkeypatch):
    _anthropic(monkeypatch, _amsg([_text("first "), _text("second")]))
    r = llm.complete("prompt", purpose="report")
    assert r.status == llm.OK and r.text == "first second"
    assert r.provider == "anthropic" and r.model == "claude-haiku-4-5"
    assert r.stop_reason == "end_turn"


def test_haiku_gets_temperature_and_no_effort(monkeypatch):
    """Haiku 4.5 accepts sampling parameters and REJECTS output_config.effort
    with a 400 — the opposite of Sonnet 5 below. Temperature travels in
    extra_body because the 1.x SDK removed the keyword."""
    stub = _anthropic(monkeypatch, _amsg([_text("ok")]))
    llm.complete("p", purpose="x", system="sys", max_tokens=400, temperature=0.3)
    sent = stub.calls[0]
    assert sent["extra_body"] == {"temperature": 0.3}
    assert "temperature" not in sent
    assert "output_config" not in sent
    assert sent["max_tokens"] == 400
    assert sent["system"] == "sys"
    assert sent["messages"] == [{"role": "user", "content": "p"}]


def test_sonnet5_gets_effort_and_headroom_but_no_temperature(monkeypatch):
    """Sonnet 5 returns 400 on temperature, and thinks by default — so a 400
    token answer budget would be spent before the answer began."""
    monkeypatch.setattr(settings, "LLM_MODEL_ANTHROPIC", "claude-sonnet-5")
    stub = _anthropic(monkeypatch, _amsg([_text("ok")]))
    llm.complete("p", purpose="x", max_tokens=400)
    sent = stub.calls[0]
    assert "temperature" not in sent and "extra_body" not in sent
    assert sent["output_config"] == {"effort": "low"}
    assert sent["max_tokens"] == 400 + llm.THINKING_HEADROOM_TOKENS


@pytest.mark.parametrize("model,temperature,effort,thinks", [
    ("claude-haiku-4-5", True, False, False),
    ("claude-haiku-4-5-20251001", True, False, False),
    ("claude-sonnet-4-6", True, True, False),
    ("claude-opus-4-6", True, True, False),
    ("claude-opus-4-8", False, True, False),
    ("claude-sonnet-5", False, True, True),
    ("claude-opus-5", False, True, True),
    ("claude-opus-5-5", False, True, True),
    ("claude-fable-5-1", False, True, True),
])
def test_request_shape_per_model_family(model, temperature, effort, thinks):
    assert llm.anthropic_caps(model) == {
        "temperature": temperature, "effort": effort, "thinks_by_default": thinks}


def test_anthropic_json_mode_instructs_and_parses_a_fenced_reply(monkeypatch):
    stub = _anthropic(monkeypatch, _amsg([_text('```json\n{"signal": "UP"}\n```')]))
    r = llm.complete("p", purpose="insight", system="be brief", json_mode=True)
    assert r.status == llm.OK and r.data == {"signal": "UP"}
    assert stub.calls[0]["system"].startswith("be brief")
    assert "JSON" in stub.calls[0]["system"]


def test_anthropic_json_mode_non_json_is_bad_response(monkeypatch):
    _anthropic(monkeypatch, _amsg([_text("sure, here you go")]))
    r = llm.complete("p", purpose="x", json_mode=True)
    assert r.status == llm.BAD_RESPONSE and r.text == "sure, here you go"


def test_bad_response_log_never_carries_the_models_raw_output(monkeypatch):
    """2026-09-28 — the model's own text can echo borrower PII straight out of
    the prompt (name, phone, address, amount). llm.bad_response used to log
    preview=text[:120]; it now logs only a length and a hash, so a PII-like
    string in the reply can never reach the log stream. The RESULT still
    carries the full text (callers need it); only the LOG record is redacted."""
    rec = _Rec()
    monkeypatch.setattr(llm, "logger", rec)
    pii_text = "Call Ramesh Kumar on 9876543210, flat 4B MG Road, owes Rs 42,000"
    _anthropic(monkeypatch, _amsg([_text(pii_text)]))
    r = llm.complete("p", purpose="x", json_mode=True)
    assert r.status == llm.BAD_RESPONSE and r.text == pii_text     # the result is unredacted

    bad_response_logs = [kw for level, event, kw in rec.events if event == "llm.bad_response"]
    assert bad_response_logs, "llm.bad_response was not logged"
    dumped = json.dumps(bad_response_logs)
    assert "Ramesh" not in dumped and "9876543210" not in dumped and "MG Road" not in dumped
    assert "preview" not in bad_response_logs[0]
    assert bad_response_logs[0]["text_length"] == len(pii_text)
    assert bad_response_logs[0]["text_sha256"] == hashlib.sha256(
        pii_text.encode(errors="surrogatepass")).hexdigest()[:16]
    # error=str(exc) is gone: json.JSONDecodeError.msg is a fixed short phrase
    # ("Expecting value") that never embeds the document, so it is safe to log.
    assert "error" not in bad_response_logs[0]
    assert bad_response_logs[0]["error_type"] == "JSONDecodeError"
    assert bad_response_logs[0]["error_msg"] == "Expecting value"


def test_bad_response_log_survives_an_exception_whose_str_embeds_its_input(monkeypatch):
    """The except clause here is (ValueError, TypeError) — broader than just
    json.JSONDecodeError. A pydantic-style ValidationError is also a
    ValueError, and ITS str() does embed the offending value
    (`input_value=<...>`). Only the error_type may be logged for anything
    that is not the one type known to be safe."""

    class _LeakyValidationError(ValueError):
        """Shaped like pydantic's ValidationError: str() echoes the input."""

        def __init__(self, bad_input: str):
            self.bad_input = bad_input

        def __str__(self) -> str:
            return (f"1 validation error for Model\n  value is not a valid dict "
                    f"[type=dict_type, input_value={self.bad_input!r}, input_type=str]")

    rec = _Rec()
    monkeypatch.setattr(llm, "logger", rec)
    pii_text = "Call Ramesh Kumar on 9876543210, flat 4B MG Road, owes Rs 42,000"
    monkeypatch.setattr(llm.json, "loads", lambda _raw: (_ for _ in ()).throw(
        _LeakyValidationError(pii_text)))
    _anthropic(monkeypatch, _amsg([_text(pii_text)]))
    r = llm.complete("p", purpose="x", json_mode=True, cache_ttl=0)
    assert r.status == llm.BAD_RESPONSE and r.text == pii_text     # the result is unredacted

    bad_response_logs = [kw for level, event, kw in rec.events if event == "llm.bad_response"]
    assert bad_response_logs, "llm.bad_response was not logged"
    dumped = json.dumps(bad_response_logs)
    assert "Ramesh" not in dumped and "9876543210" not in dumped and "MG Road" not in dumped
    assert "input_value" not in dumped                              # str(exc) never went out
    assert "error" not in bad_response_logs[0]
    assert bad_response_logs[0]["error_type"] == "_LeakyValidationError"
    assert "error_msg" not in bad_response_logs[0]                  # not the safe type — no message at all


def test_json_schema_uses_structured_outputs_on_anthropic(monkeypatch):
    schema = {"type": "object", "properties": {"amount": {"type": "number"}},
              "required": ["amount"], "additionalProperties": False}
    stub = _anthropic(monkeypatch, _amsg([_text('{"amount": 5000}')]))
    r = llm.complete("p", purpose="extract", json_schema=schema)
    assert r.data == {"amount": 5000}
    assert stub.calls[0]["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert "JSON" not in (stub.calls[0].get("system") or "")   # no belt-and-braces prose


def test_json_schema_on_openai_compatible_rides_in_the_system_prompt(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    stub = _openai(monkeypatch, _oresp('{"amount": 1}'))
    schema = {"type": "object", "properties": {"amount": {"type": "number"}}}
    r = llm.complete("p", purpose="extract", system="sys", json_schema=schema)
    assert r.data == {"amount": 1}
    sent = stub.calls[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0]["role"] == "system"
    assert json.dumps(schema) in sent["messages"][0]["content"]


def test_schema_is_part_of_the_cache_key_and_absent_schema_keeps_old_keys():
    old = "llm:cache:p:" + __import__("hashlib").sha256(
        "m\x00s\x00q".encode()).hexdigest()[:32]
    assert llm._cache_key("p", "m", "q", "s") == old
    assert llm._cache_key("p", "m", "q", "s", {"type": "object"}) != old


def test_anthropic_400_is_invalid_request_and_not_retried(monkeypatch):
    stub = _anthropic(monkeypatch, _http_err(400))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.INVALID_REQUEST
    assert len(stub.calls) == 1


def test_402_is_billing(monkeypatch):
    _anthropic(monkeypatch, _http_err(402))
    assert llm.complete("p", purpose="x").status == llm.BILLING


def test_overloaded_529_is_retried(monkeypatch):
    stub = _anthropic(monkeypatch, lambda _k, n: _http_err(529) if n == 1 else _amsg([_text("ok")]))
    assert llm.complete("p", purpose="x").status == llm.OK
    assert len(stub.calls) == 2


def test_refusal_is_named_not_cached_and_not_ai_generated(monkeypatch):
    stub = _anthropic(monkeypatch, _amsg([], stop="refusal",
                                         stop_details=NS(category="cyber", explanation="")))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.REFUSED and r.ai_generated is False
    assert "cyber" in r.failure_reason
    llm.complete("p", purpose="x")
    assert len(stub.calls) == 2                      # never served from cache


@pytest.mark.parametrize("provider", ["anthropic", "groq"])
def test_budget_spent_before_any_answer_is_bad_response(monkeypatch, provider):
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    if provider == "anthropic":
        _anthropic(monkeypatch, _amsg([], stop="max_tokens"))
    else:
        _openai(monkeypatch, _oresp("", finish="length"))
    r = llm.complete("p", purpose="x")
    assert r.status == llm.BAD_RESPONSE and r.stop_reason == "max_tokens"


# ── the openai path is byte-compatible for existing callers ─────────────────
def test_existing_json_mode_call_sends_exactly_what_it_did(monkeypatch):
    """H14 and the six original features call complete(json_mode=True) on groq.
    The request they produce must not gain or lose a key."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    stub = _openai(monkeypatch, _oresp('{"a": 1}'))
    llm.complete("user text", purpose="x", system="sys", json_mode=True,
                 max_tokens=300, temperature=0.2)
    assert stub.calls[0] == {
        "model": "groq-model",
        "messages": [{"role": "system", "content": "sys"},
                     {"role": "user", "content": "user text"}],
        "max_tokens": 300, "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "extra_body": {"reasoning_effort": "low"},
    }


# ── the SDKs no longer retry underneath the seam ─────────────────────────────
def test_every_sdk_client_is_built_without_its_own_retries(monkeypatch):
    """Both SDKs default to max_retries=2. Under this module's own two retries
    that made up to 9 requests per call, six of them invisible to it."""
    import anthropic
    import openai
    built = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: built.append(("openai", kw)))
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: built.append(("anthropic", kw)))
    llm._client("groq", "k")
    llm._client("openai", "k")
    llm._anthropic_client("k")
    assert [kw["max_retries"] for _, kw in built] == [0, 0, 0]


# ── the fallback provider ────────────────────────────────────────────────────
def test_fallback_serves_when_the_primary_has_no_key(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _openai(monkeypatch, _oresp("from groq"))
    r = llm.complete("p", purpose="fb1")
    assert r.status == llm.OK and r.provider == "groq"
    assert r.fallback_from == "anthropic"
    # FALLBACK_OK, not OK: the counters say the primary did not answer this.
    assert llm.stats()["fb1"] == {llm.NOT_CONFIGURED: 1, llm.FALLBACK_OK: 1}
    h = llm.health()
    assert h["usable"] is True and h["primary_usable"] is False
    assert "No API key" in h["unusable_reason"]


def test_fallback_serves_when_the_primary_fails_on_its_side(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _anthropic(monkeypatch, _http_err(401))
    _openai(monkeypatch, _oresp("from groq"))
    r = llm.complete("p", purpose="fb2")
    assert r.provider == "groq" and r.fallback_from == "anthropic"


def test_no_fallback_on_a_caller_shaped_failure(monkeypatch):
    """Bad JSON is the request's problem. Trying another model would bury it."""
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _anthropic(monkeypatch, _amsg([_text("not json")]))
    groq = _openai(monkeypatch, _oresp('{"a": 1}'))
    r = llm.complete("p", purpose="fb3", json_mode=True)
    assert r.status == llm.BAD_RESPONSE and r.provider == "anthropic"
    assert groq.calls == []


def test_fallback_equal_to_primary_is_not_tried_twice(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "anthropic")
    stub = _anthropic(monkeypatch, _http_err(401))
    llm.complete("p", purpose="fb4")
    assert len(stub.calls) == 1


# ── chat() on Anthropic ──────────────────────────────────────────────────────
TOOLS = [llm.ToolSpec("get_kpi", "Read one KPI by id",
                      {"type": "object", "properties": {"kpi_id": {"type": "string"}},
                       "required": ["kpi_id"], "additionalProperties": False},
                      strict=True),
         llm.ToolSpec("list_agencies", "List agencies", {"type": "object", "properties": {}})]


def test_anthropic_tool_turn(monkeypatch):
    thinking = NS(type="thinking", thinking="", signature="sig-abc")
    stub = _anthropic(monkeypatch, _amsg(
        [thinking, _text("Checking."), _tool_use("tu_1", "get_kpi", {"kpi_id": "npa_pct"})],
        stop="tool_use"))
    r = llm.chat([{"role": "user", "content": "What is NPA?"}], purpose="copilot",
                 system="You are the portfolio copilot.", tools=TOOLS)
    assert r.status == llm.OK and r.wants_tools
    assert r.tool_calls == [llm.ToolCall("tu_1", "get_kpi", {"kpi_id": "npa_pct"})]
    assert r.text == "Checking." and r.stop_reason == "tool_use"
    assert r.model == "claude-sonnet-5"
    assert r.usage.input_tokens == 11 and r.usage.output_tokens == 7
    assert r.provider_content[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}

    sent = stub.calls[0]
    assert sent["system"] == "You are the portfolio copilot."
    assert sent["tools"][0] == {"name": "get_kpi", "description": "Read one KPI by id",
                                "input_schema": TOOLS[0].parameters, "strict": True}
    assert "strict" not in sent["tools"][1]
    assert sent["output_config"] == {"effort": "medium"}
    assert "temperature" not in sent and "extra_body" not in sent


def test_effort_can_be_overridden_per_call(monkeypatch):
    stub = _anthropic(monkeypatch, _amsg([_text("done")]))
    llm.chat([{"role": "user", "content": "q"}], purpose="x", effort="high")
    assert stub.calls[0]["output_config"] == {"effort": "high"}


def test_a_full_turn_round_trips_in_anthropic_format(monkeypatch):
    """The assistant turn goes back verbatim (thinking signature included), and
    both tool results go back in ONE user message."""
    first = _amsg([NS(type="thinking", thinking="", signature="s1"),
                   _tool_use("a", "get_kpi", {"kpi_id": "x"}),
                   _tool_use("b", "list_agencies", {})], stop="tool_use")
    stub = _anthropic(monkeypatch, lambda _k, n: first if n == 1 else _amsg([_text("NPA is 4%")]))
    history = [{"role": "user", "content": "q"}]
    r1 = llm.chat(history, purpose="x", tools=TOOLS)
    history += [r1.assistant_message(),
                llm.tool_result(r1.tool_calls[0], "4.0"),
                llm.tool_result(r1.tool_calls[1], "timeout", is_error=True)]
    r2 = llm.chat(history, purpose="x", tools=TOOLS)
    assert r2.text == "NPA is 4%" and not r2.wants_tools

    msgs = stub.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "s1"}
    assert msgs[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "content": "4.0"},
        {"type": "tool_result", "tool_use_id": "b", "content": "timeout", "is_error": True},
    ]


def test_an_assistant_turn_from_another_provider_is_rebuilt_as_blocks():
    msgs = llm._to_anthropic([
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "looking", "provider": "groq",
         "tool_calls": [llm.ToolCall("c1", "get_kpi", {"kpi_id": "x"})]},
        llm.tool_result("c1", "1"),
    ])
    assert msgs[1]["content"] == [
        {"type": "text", "text": "looking"},
        {"type": "tool_use", "id": "c1", "name": "get_kpi", "input": {"kpi_id": "x"}},
    ]


def test_chat_is_never_cached(monkeypatch):
    stub = _anthropic(monkeypatch, _amsg([_text("a")]))
    for _ in range(2):
        llm.chat([{"role": "user", "content": "same"}], purpose="x")
    assert len(stub.calls) == 2


def test_chat_retries_a_rate_limit(monkeypatch):
    stub = _anthropic(monkeypatch, lambda _k, n: _http_err(429) if n == 1 else _amsg([_text("ok")]))
    assert llm.chat([{"role": "user", "content": "q"}], purpose="x").status == llm.OK
    assert len(stub.calls) == 2


def test_chat_not_configured_and_refused(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    assert llm.chat([{"role": "user", "content": "q"}], purpose="x").status == llm.NOT_CONFIGURED
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    _anthropic(monkeypatch, _amsg([], stop="refusal", stop_details=NS(category=None)))
    r = llm.chat([{"role": "user", "content": "q"}], purpose="x")
    assert r.status == llm.REFUSED and not r.ai_generated and not r.wants_tools


def test_chat_falls_back_to_groq(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _anthropic(monkeypatch, _http_err(503))
    _openai(monkeypatch, _oresp("from groq"))
    r = llm.chat([{"role": "user", "content": "q"}], purpose="x")
    assert r.provider == "groq" and r.fallback_from == "anthropic" and r.text == "from groq"


# ── chat() on OpenAI-compatible providers ────────────────────────────────────
def _otc(id_, name, args):
    return NS(id=id_, type="function", function=NS(name=name, arguments=args))


def test_openai_tool_turn_and_translation(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    stub = _openai(monkeypatch, _oresp(None, [_otc("c1", "get_kpi", '{"kpi_id": "npa"}'),
                                              _otc("c2", "list_agencies", "{not json")],
                                       finish="tool_calls"))
    r = llm.chat([{"role": "user", "content": "q"},
                  {"role": "assistant", "content": "", "tool_calls": [llm.ToolCall("p1", "get_kpi", {"kpi_id": "a"})]},
                  llm.tool_result("p1", "boom", is_error=True)],
                 purpose="x", system="sys", tools=TOOLS)
    assert r.stop_reason == "tool_use"
    assert r.tool_calls[0] == llm.ToolCall("c1", "get_kpi", {"kpi_id": "npa"})
    assert r.tool_calls[1].arguments == {} and "not valid JSON" in r.tool_calls[1].arguments_error
    assert r.usage.input_tokens == 5 and r.usage.output_tokens == 3

    sent = stub.calls[0]
    assert sent["tools"][0] == {"type": "function", "function": {
        "name": "get_kpi", "description": "Read one KPI by id", "parameters": TOOLS[0].parameters}}
    m = sent["messages"]
    assert m[0] == {"role": "system", "content": "sys"}
    assert m[2]["tool_calls"][0]["function"] == {"name": "get_kpi", "arguments": '{"kpi_id": "a"}'}
    assert m[3] == {"role": "tool", "tool_call_id": "p1", "content": "ERROR: boom"}


# ── FakeLLM ──────────────────────────────────────────────────────────────────
def test_fake_serves_complete_through_the_real_seam():
    with llm.use_fake(["plain", {"k": 1}, llm.FakeLLM.call("unused")]) as fake:
        a = llm.complete("p1", purpose="f")
        b = llm.complete("p2", purpose="f", json_mode=True)
        assert (a.status, a.text, a.provider) == (llm.OK, "plain", "fake")
        assert b.data == {"k": 1}
        assert [c["kind"] for c in fake.calls] == ["complete", "complete"]
    assert llm.resolved_provider()[0] == "anthropic"      # uninstalled on exit


def test_fake_exceptions_are_classified_and_retried():
    with llm.use_fake([_http_err(429), "recovered"]) as fake:
        assert llm.complete("p", purpose="f2", cache_ttl=0).status == llm.OK
        assert len(fake.calls) == 2


def test_fake_drives_a_tool_loop():
    """The shape F02's runtime will run: call, execute, answer, repeat."""
    script = [llm.FakeLLM.call("get_kpi", {"kpi_id": "npa"}), "NPA is 4.0%"]
    with llm.use_fake(script) as fake:
        history = [{"role": "user", "content": "q"}]
        while True:
            r = llm.chat(history, purpose="loop", tools=TOOLS)
            if not r.wants_tools:
                break
            history.append(r.assistant_message())
            history += [llm.tool_result(c, "4.0") for c in r.tool_calls]
    assert r.text == "NPA is 4.0%"
    assert fake.calls[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_get_kpi",
                                             "content": "4.0", "is_error": False}


def test_an_exhausted_script_fails_the_test_loudly():
    """Swallowed into UPSTREAM_ERROR it would read as a flaky provider."""
    with llm.use_fake([]):
        with pytest.raises(llm.FakeScriptExhausted):
            llm.complete("p", purpose="f3", cache_ttl=0)


# ── health ───────────────────────────────────────────────────────────────────
def test_health_names_both_tiers_and_the_fallback_without_the_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    h = llm.health()
    assert h["active_provider"] == "anthropic"
    assert (h["model"], h["agent_model"]) == ("claude-haiku-4-5", "claude-sonnet-5")
    assert h["fallback_provider"] == "groq" and h["fallback_usable"] is True
    assert "sk-ant-test" not in json.dumps(h) and "gsk-test" not in json.dumps(h)




# ── the real SDKs, through a mock HTTP transport ─────────────────────────────
# The constructors are WRAPPED, not replaced: whatever production passes
# (max_retries, timeout, base_url) reaches the real SDK; only the transport
# is swapped for an in-process one.
def _real_anthropic(monkeypatch, handler, built=None):
    import anthropic
    import httpx2
    real = anthropic.Anthropic

    def ctor(**kw):
        if built is not None:
            built.append(kw)
        return real(**kw, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(anthropic, "Anthropic", ctor)


def _real_openai(monkeypatch, handler, built=None):
    import httpx
    import openai
    real = openai.OpenAI

    def ctor(**kw):
        if built is not None:
            built.append(kw)
        return real(**kw, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(openai, "OpenAI", ctor)


def _anthropic_message(body, content, stop):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 20, "output_tokens": 9}}


def test_real_anthropic_sdk_accepts_the_request_and_parses_the_reply(monkeypatch):
    """A stub cannot catch a misspelt parameter; the SDK's own serialiser and
    response models can. Runs complete(json_schema) and a chat tool turn."""
    import httpx2

    seen: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if body.get("tools"):
            content = [{"type": "thinking", "thinking": "", "signature": "sig"},
                       {"type": "tool_use", "id": "toolu_1", "name": "get_kpi",
                        "input": {"kpi_id": "npa_pct"}}]
            return httpx2.Response(200, json=_anthropic_message(body, content, "tool_use"))
        return httpx2.Response(200, json=_anthropic_message(
            body, [{"type": "text", "text": '{"amount": 2500}'}], "end_turn"))

    _real_anthropic(monkeypatch, handler)
    schema = {"type": "object", "properties": {"amount": {"type": "number"}},
              "required": ["amount"], "additionalProperties": False}
    r = llm.complete("PTP of 2500 on Friday", purpose="extract", json_schema=schema)
    assert r.status == llm.OK and r.data == {"amount": 2500}

    c = llm.chat([{"role": "user", "content": "npa?"}], purpose="copilot", tools=TOOLS)
    assert c.wants_tools and c.tool_calls[0].arguments == {"kpi_id": "npa_pct"}
    assert c.provider_content[0]["signature"] == "sig"

    assert seen[0]["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert seen[0]["temperature"] == 0.3        # Haiku: extra_body lands top-level
    assert seen[1]["tools"][0]["input_schema"] == TOOLS[0].parameters
    assert seen[1]["output_config"] == {"effort": "medium"}
    assert "temperature" not in seen[1]         # Sonnet 5 would 400 on it


def test_real_openai_sdk_accepts_the_tool_request(monkeypatch):
    import httpx

    seen: list[dict] = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 0, "model": "groq-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "get_kpi", "arguments": '{"kpi_id": "npa"}'}}]}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}})

    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    _real_openai(monkeypatch, handler)
    r = llm.chat([{"role": "user", "content": "npa?"}], purpose="copilot", tools=TOOLS)
    assert r.tool_calls == [llm.ToolCall("call_1", "get_kpi", {"kpi_id": "npa"})]
    assert r.wants_tools
    assert seen[0]["tools"][0]["function"]["name"] == "get_kpi"
    # The SDK merges extra_body into the top level of the JSON body.
    assert seen[0]["reasoning_effort"] == "low"


@pytest.mark.parametrize("provider", ["groq", "openai", "anthropic"])
def test_a_persistent_429_costs_exactly_the_seams_own_attempts(monkeypatch, provider):
    """The 9 -> 3 measurement, executable. Production's constructor kwargs go
    to the real SDK; if max_retries=0 were dropped, the SDK would retry under
    the seam and this would count 9 requests, not 3."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    hits: list[int] = []
    if provider == "anthropic":
        import httpx2 as h
    else:
        import httpx as h

    def handler(request):
        hits.append(1)
        return h.Response(429, headers={"retry-after": "0"},
                          json={"error": {"type": "rate_limit_error", "message": "slow down"}})

    (_real_anthropic if provider == "anthropic" else _real_openai)(monkeypatch, handler)
    r = llm.complete("p", purpose="r429", cache_ttl=0)
    assert r.status == llm.RATE_LIMITED
    assert len(hits) == settings.LLM_MAX_RETRIES + 1


def test_chat_gets_the_agent_timeout_and_complete_the_short_one(monkeypatch):
    import httpx2
    built: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        return httpx2.Response(200, json=_anthropic_message(
            body, [{"type": "text", "text": "ok"}], "end_turn"))

    _real_anthropic(monkeypatch, handler, built)
    assert llm.complete("p", purpose="t1", cache_ttl=0).status == llm.OK
    assert llm.chat([{"role": "user", "content": "q"}], purpose="t2").status == llm.OK
    assert [kw["timeout"] for kw in built] == [20.0, 120.0]
    assert [kw["max_retries"] for kw in built] == [0, 0]


# ── audit fix-ups (coordinator audit of 9204955) ─────────────────────────────
class _Rec:
    """Records structlog calls, so a log field can be asserted on."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def __getattr__(self, level):
        return lambda event, **kw: self.events.append((level, event, kw))


def test_a_malformed_history_is_invalid_request_not_an_outage(monkeypatch):
    """A KeyError while BUILDING the request used to be UPSTREAM_ERROR: retried
    three times, then quietly served by the fallback."""
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    anth = _anthropic(monkeypatch, _amsg([_text("x")]))
    groq = _openai(monkeypatch, _oresp("x"))
    r = llm.chat([{"role": "tool", "content": "no tool_call_id"}], purpose="bad1")
    assert r.status == llm.INVALID_REQUEST and "KeyError" in r.failure_reason
    assert anth.calls == [] and groq.calls == [] and r.fallback_from is None


def test_an_unknown_tool_call_key_is_invalid_request(monkeypatch):
    anth = _anthropic(monkeypatch, _amsg([_text("x")]))
    r = llm.chat([{"role": "user", "content": "q"},
                  {"role": "assistant", "content": "",
                   "tool_calls": [{"id": "a", "name": "get_kpi", "argz": {}}]}],
                 purpose="bad2")
    assert r.status == llm.INVALID_REQUEST and anth.calls == []


def test_an_sdk_keyword_rejection_is_invalid_request_and_not_retried(monkeypatch):
    """Exactly the anthropic 1.x `temperature` TypeError, had it shipped."""
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    anth = _anthropic(monkeypatch, TypeError(
        "Messages.create() got an unexpected keyword argument 'temperature'"))
    groq = _openai(monkeypatch, _oresp("x"))
    r = llm.complete("p", purpose="kw")
    assert r.status == llm.INVALID_REQUEST
    assert len(anth.calls) == 1 and groq.calls == []


def test_413_is_invalid_request_and_not_retried(monkeypatch):
    anth = _anthropic(monkeypatch, _http_err(413))
    assert llm.complete("p", purpose="big").status == llm.INVALID_REQUEST
    assert len(anth.calls) == 1


def test_an_unreadable_reply_is_bad_response_not_retried_not_fallen_back(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "anthropic")
    groq = _openai(monkeypatch, _oresp(None, [NS(id="c", type="function", function=None)],
                                       finish="tool_calls"))
    anth = _anthropic(monkeypatch, _amsg([_text("x")]))
    r = llm.chat([{"role": "user", "content": "q"}], purpose="unread", tools=TOOLS)
    assert r.status == llm.BAD_RESPONSE and "could not read" in r.failure_reason
    assert len(groq.calls) == 1 and anth.calls == []


def test_a_schema_that_cannot_be_serialised_is_invalid_request_not_a_raise(monkeypatch):
    anth = _anthropic(monkeypatch, _amsg([_text("{}")]))
    r = llm.complete("p", purpose="s", json_schema={"type": "object", "bad": object()},
                     cache_ttl=0)
    assert r.status == llm.INVALID_REQUEST and anth.calls == []


def test_a_missing_sdk_is_not_configured_and_health_says_why(monkeypatch):
    """The state of an un-rebuilt image: it used to classify as MODEL_NOT_FOUND
    ("notfound" in "modulenotfounderror") while health() read usable."""
    import sys
    monkeypatch.setitem(sys.modules, "anthropic", None)
    r = llm.complete("p", purpose="nosdk")
    assert r.status == llm.NOT_CONFIGURED and "not installed" in r.failure_reason
    h = llm.health()
    assert h["usable"] is False and h["primary_usable"] is False
    assert "not installed" in h["unusable_reason"]

    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _openai(monkeypatch, _oresp("from groq"))
    r2 = llm.complete("p", purpose="nosdk", cache_ttl=0)
    assert r2.provider == "groq" and r2.fallback_from == "anthropic"
    assert llm.health()["usable"] is True


def test_an_sdk_that_fails_to_import_is_not_configured_not_model_not_found(monkeypatch):
    import sys
    monkeypatch.setattr(llm, "_sdk_missing", lambda _name: None)   # the spec is there...
    monkeypatch.setitem(sys.modules, "anthropic", None)             # ...the import fails
    r = llm.complete("p", purpose="imp")
    assert r.status == llm.NOT_CONFIGURED and "could not be imported" in r.failure_reason
    assert llm._classify(ModuleNotFoundError("No module named 'anthropic'")) == llm.NOT_CONFIGURED


def test_a_tool_call_cut_off_by_max_tokens_is_not_runnable(monkeypatch):
    _anthropic(monkeypatch, _amsg([_tool_use("t1", "get_kpi", {"kpi_"})], stop="max_tokens"))
    r = llm.chat([{"role": "user", "content": "q"}], purpose="cut", tools=TOOLS)
    assert r.status == llm.BAD_RESPONSE and not r.wants_tools
    assert "cut off" in r.failure_reason


@pytest.mark.parametrize("provider", ["anthropic", "groq"])
def test_an_empty_turn_that_hit_max_tokens_is_bad_response(monkeypatch, provider):
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    if provider == "anthropic":
        _anthropic(monkeypatch, _amsg([], stop="max_tokens"))
    else:
        _openai(monkeypatch, _oresp("", finish="length"))
    r = llm.chat([{"role": "user", "content": "q"}], purpose="empty")
    assert r.status == llm.BAD_RESPONSE and r.ai_generated is False


def test_wants_tools_requires_a_tool_use_stop():
    calls = [llm.ToolCall("a", "get_kpi")]
    assert llm.ChatResult(status=llm.OK, tool_calls=calls, stop_reason="tool_use").wants_tools
    assert not llm.ChatResult(status=llm.OK, tool_calls=calls, stop_reason="end_turn").wants_tools
    assert not llm.ChatResult(status=llm.OK, tool_calls=calls, stop_reason="max_tokens").wants_tools


def test_a_scripted_max_tokens_turn_is_bad_response_through_the_fake():
    cut = llm.ChatResult(status=llm.OK, tool_calls=[llm.ToolCall("a", "get_kpi")],
                         stop_reason="max_tokens", provider="fake")
    with llm.use_fake([cut]):
        r = llm.chat([{"role": "user", "content": "q"}], purpose="fake-cut")
    assert r.status == llm.BAD_RESPONSE and not r.wants_tools


def test_openai_tool_calls_finishing_with_stop_are_still_a_tool_turn(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    _openai(monkeypatch, _oresp(None, [NS(id="c1", type="function",
                                          function=NS(name="get_kpi", arguments="{}"))],
                                finish="stop"))
    r = llm.chat([{"role": "user", "content": "q"}], purpose="stopcalls", tools=TOOLS)
    assert r.stop_reason == "tool_use" and r.wants_tools


def test_provider_none_is_a_kill_switch_even_with_a_fallback(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "none")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    groq = _openai(monkeypatch, _oresp("must not be called"))
    r = llm.complete("p", purpose="kill")
    assert r.status == llm.NOT_CONFIGURED and "switched off" in r.failure_reason
    assert llm.chat([{"role": "user", "content": "q"}], purpose="kill").status == llm.NOT_CONFIGURED
    assert groq.calls == []
    h = llm.health()
    assert h["usable"] is False and h["fallback_provider"] is None


def test_a_fenced_reply_on_groq_is_still_bad_response(monkeypatch):
    """Fence-stripping is Anthropic-only; the groq JSON contract is unchanged."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    _openai(monkeypatch, _oresp('```json\n{"a": 1}\n```'))
    assert llm.complete("p", purpose="fence", json_mode=True).status == llm.BAD_RESPONSE


def test_llm_ok_logs_the_attempt_that_succeeded(monkeypatch):
    rec = _Rec()
    monkeypatch.setattr(llm, "logger", rec)
    _anthropic(monkeypatch, lambda _k, n: _http_err(429) if n == 1 else _amsg([_text("ok")]))
    llm.complete("p", purpose="att")
    ok = [kw for level, event, kw in rec.events if event == "llm.ok"]
    assert ok and ok[0]["attempt"] == 1


def test_when_both_fail_the_primary_failure_is_returned_with_the_fallbacks(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _openai(monkeypatch, _http_err(503))
    r = llm.complete("p", purpose="both", cache_ttl=0)
    assert r.status == llm.NOT_CONFIGURED and "No API key" in r.failure_reason
    assert r.fallback_failure["provider"] == "groq"
    assert r.fallback_failure["status"] == llm.UPSTREAM_ERROR
    c = llm.chat([{"role": "user", "content": "q"}], purpose="both")
    assert c.status == llm.NOT_CONFIGURED and c.fallback_failure["status"] == llm.UPSTREAM_ERROR


def test_groq_content_filter_in_complete_is_refused(monkeypatch):
    """It was OK, usually over an empty string; chat() already said REFUSED."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    _openai(monkeypatch, _oresp("", finish="content_filter"))
    r = llm.complete("p", purpose="cf")
    assert r.status == llm.REFUSED and r.ai_generated is False


def test_use_fake_never_touches_the_real_store():
    before = llm._store
    with llm.use_fake(["x"]):
        assert isinstance(llm._store, llm._MemoryStore) and llm._store is not before
        llm.complete("p", purpose="fk")
    assert llm._store is before


# ── second audit fix-up (A-E, G) ─────────────────────────────────────────────
class _APITimeoutError(Exception):
    """Stands in for the real anthropic/openai SDK timeout exception classes —
    _classify matches on the CLASS NAME containing "timeout", not on the
    concrete type, exactly so a stub like this one exercises the real path."""


# -- A: only an ai_generated fallback result is ever served ------------------
def test_fallback_bad_response_does_not_hide_the_primarys_failure(monkeypatch):
    """Before this fix, a fallback that failed in a CALLER-shaped way (here:
    non-JSON for a json_mode request) was returned as though it had answered,
    silently dropping the primary's NOT_CONFIGURED — the failure that
    actually needed fixing."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    groq = _openai(monkeypatch, _oresp("not json at all"))
    r = llm.complete("p", purpose="fbcaller", json_mode=True, cache_ttl=0)
    assert r.status == llm.NOT_CONFIGURED and r.provider == "none"
    assert r.ai_generated is False
    assert r.fallback_failure == {"provider": "groq", "status": llm.BAD_RESPONSE,
                                  "failure_reason": "Model did not return valid JSON"}
    assert len(groq.calls) == 1                      # the fallback WAS tried


def test_chat_fallback_refused_does_not_hide_the_primarys_failure(monkeypatch):
    """Same defect, in chat(): a REFUSED fallback (groq's content_filter) must
    not be served in place of the primary's own failure."""
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _anthropic(monkeypatch, _http_err(503))
    groq = _openai(monkeypatch, _oresp("", finish="content_filter"))
    r = llm.chat([{"role": "user", "content": "q"}], purpose="fbcallerchat")
    assert r.status == llm.UPSTREAM_ERROR and r.provider == "anthropic"
    assert r.ai_generated is False
    assert r.fallback_failure["provider"] == "groq"
    assert r.fallback_failure["status"] == llm.REFUSED
    assert len(groq.calls) == 1


# -- B: chat() never falls back on TIMEOUT; complete() still does ------------
def test_chat_does_not_fall_back_on_timeout_but_complete_does(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _anthropic(monkeypatch, _APITimeoutError("simulated timeout"))
    groq = _openai(monkeypatch, _oresp("from groq"))

    r = llm.chat([{"role": "user", "content": "q"}], purpose="timeoutchat")
    assert r.status == llm.TIMEOUT and r.provider == "anthropic"
    assert groq.calls == []                           # never even tried

    r2 = llm.complete("p", purpose="timeoutcomplete", cache_ttl=0)
    assert r2.status == llm.OK and r2.provider == "groq"
    assert r2.fallback_from == "anthropic"
    assert len(groq.calls) == 1


# -- C: a FALLBACK attempt's status records under its own dimension ----------
def test_fallback_failure_is_counted_under_its_own_dimension(monkeypatch):
    """A fallback FAILURE used to share the primary's counter key, so a
    struggling fallback and a struggling primary were indistinguishable in
    the same count."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "groq")
    _openai(monkeypatch, _http_err(503))
    llm.complete("p", purpose="ctrboth", cache_ttl=0)
    assert llm.stats()["ctrboth"] == {llm.NOT_CONFIGURED: 1, "FALLBACK_UPSTREAM_ERROR": 1}


# -- D: _cache_key hardening ---------------------------------------------------
def test_a_lone_surrogate_in_the_prompt_does_not_raise_or_invalidate(monkeypatch):
    """A bare .encode() on a lone surrogate raises UnicodeEncodeError, which
    the call site used to catch and misreport as an unserialisable SCHEMA —
    wrong, since there may be no schema at all. errors="surrogatepass" makes
    the prompt harmless to the key computation."""
    _anthropic(monkeypatch, _amsg([_text("ok")]))
    r = llm.complete("bad \ud800 prompt", purpose="surrogate", cache_ttl=0)
    assert r.status == llm.OK
    assert r.status != llm.INVALID_REQUEST


def test_a_deeply_nested_schema_is_invalid_request_not_a_raise(monkeypatch):
    """json.dumps on a ~5000-level schema raises RecursionError, which the old
    guard (TypeError, ValueError) did not catch — it would have propagated out
    of complete() as an unhandled exception, breaking the "never raises"
    contract this whole module exists to hold."""
    schema: dict = {"type": "object"}
    node = schema
    for _ in range(5000):
        child: dict = {"type": "object"}
        node["properties"] = {"x": child}
        node = child
    anth = _anthropic(monkeypatch, _amsg([_text("{}")]))
    r = llm.complete("p", purpose="deepschema", json_schema=schema, cache_ttl=0)
    assert r.status == llm.INVALID_REQUEST
    assert anth.calls == []                            # never reached the SDK


# -- E: _sdk_missing distinguishes "not installed" from "installed but broken"
def test_an_sdk_present_but_broken_on_import_is_distinguished_from_missing(monkeypatch):
    """find_spec only proves the package is ON DISK. A package present but
    broken on import (an incompatible dependency, a bad upgrade) used to read
    as usable and fail later, misclassified as a provider outage."""
    real_import_module = llm.importlib.import_module

    def broken(name, *a, **kw):
        if name == "anthropic":
            raise RuntimeError("boom")
        return real_import_module(name, *a, **kw)

    monkeypatch.setattr(llm.importlib, "import_module", broken)
    h = llm.health()
    assert h["usable"] is False and h["primary_usable"] is False
    assert "failed to import" in h["unusable_reason"]
    assert "RuntimeError" in h["unusable_reason"] and "boom" in h["unusable_reason"]
