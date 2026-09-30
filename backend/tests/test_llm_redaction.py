"""No borrower identifier crosses the core/llm.py seam (F02's redaction, wired
for the live features). The seam is the only place this can be guaranteed:
a rule the six call sites each had to remember is a rule that will be
forgotten.

Driven through the same stubbed client as tests/test_llm.py, so nothing here
needs a key or the network.
"""
from __future__ import annotations

import json

import pytest

from app.core import llm
from app.core.config import settings

PROMPT = (
    "Customer: Farhan Siddiqui (SALARIED, Gurugram)\n"
    "Customer said: \"call me on 9899000101, my PAN is ABCDE1234F\"\n"
    "Overdue Rs 24,600 at 47 DPD on loan MTB0000001."
)


class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _Captured:
    """The stub client, keeping every prompt it was asked to send."""

    def __init__(self, reply):
        self.sent: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.sent.append(kwargs)
                return _Resp(reply(kwargs) if callable(reply) else reply)

        self.chat = type("C", (), {"completions": _Completions()})()

    @property
    def text(self) -> str:
        return json.dumps(self.sent)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "test-model")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    monkeypatch.setattr(settings, "LLM_CACHE_TTL_SECONDS", 60)
    monkeypatch.setattr(llm, "_store", llm._MemoryStore())
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    yield
    llm._store = None


def _use(monkeypatch, reply):
    client = _Captured(reply)
    monkeypatch.setattr(llm, "_client", lambda *_a, **_k: client)
    return client


def test_the_provider_never_sees_the_identifiers(monkeypatch):
    client = _use(monkeypatch, "noted")
    out = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    assert out.status == llm.OK
    for secret in ("Farhan Siddiqui", "9899000101", "ABCDE1234F"):
        assert secret not in client.text
    assert "[name-1]" in client.text and "[phone-1]" in client.text and "[pan-1]" in client.text
    # What the model DOES need is untouched.
    assert "24,600" in client.text and "47 DPD" in client.text and "Gurugram" in client.text


def test_the_answer_comes_back_with_the_real_values(monkeypatch):
    """A brief that says "greet [name-1]" is no use at a door."""
    _use(monkeypatch, "Greet [name-1] warmly; if pressed, call [phone-1].")
    out = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    assert out.text == "Greet Farhan Siddiqui warmly; if pressed, call 9899000101."


def test_a_token_the_model_recapitalised_is_still_restored(monkeypatch):
    _use(monkeypatch, "[Name-1] asked for time.")
    out = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    assert out.text == "Farhan Siddiqui asked for time."


def test_json_answers_are_restored_throughout(monkeypatch):
    _use(monkeypatch, json.dumps({
        "opening_line": "[name-1] ji, namaste",
        "risk_flags": ["reach [name-1] on [phone-1]"],
    }))
    out = llm.complete(PROMPT, purpose="visit_strategy", json_mode=True, names=["Farhan Siddiqui"])
    assert out.data["opening_line"] == "Farhan Siddiqui ji, namaste"
    assert out.data["risk_flags"] == ["reach Farhan Siddiqui on 9899000101"]


def test_the_result_says_how_much_was_removed(monkeypatch):
    _use(monkeypatch, "noted")
    out = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    assert out.redactions == {"name": 1, "phone": 1, "pan": 1}


def test_the_fallback_provider_gets_the_redacted_prompt_too(monkeypatch):
    """The fallback is a second chance to leak, and it used to be one."""
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL_OPENAI", "gpt-4o-mini")
    seen: list[dict] = []

    class _Client:
        def __init__(self, fail: bool):
            outer = self

            class _Completions:
                def create(self, **kwargs):
                    seen.append(kwargs)
                    if fail:
                        raise TimeoutError("simulated")
                    return _Resp("ok from the fallback")
            self.chat = type("C", (), {"completions": _Completions()})()

    clients = iter([_Client(fail=True), _Client(fail=False)])
    monkeypatch.setattr(llm, "_client", lambda *_a, **_k: next(clients))
    out = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    assert out.ai_generated and len(seen) >= 2
    blob = json.dumps(seen)
    for secret in ("Farhan Siddiqui", "9899000101", "ABCDE1234F"):
        assert secret not in blob


def test_two_borrowers_do_not_share_a_cached_answer(monkeypatch):
    """The prompts differ ONLY in the values just replaced. Keyed on the
    pseudonymised text they would collide, and one borrower would be handed
    another's brief."""
    replies = iter(["brief for [name-1]", "brief for [name-1]"])
    client = _use(monkeypatch, lambda _kw: next(replies))
    a = llm.complete("Customer: Farhan Siddiqui, 47 DPD", purpose="visit_strategy",
                     names=["Farhan Siddiqui"])
    b = llm.complete("Customer: Sunita Rawat, 47 DPD", purpose="visit_strategy",
                     names=["Sunita Rawat"])
    assert a.text == "brief for Farhan Siddiqui"
    assert b.text == "brief for Sunita Rawat"
    assert len(client.sent) == 2 and not b.cached


def test_the_same_borrower_still_hits_the_cache_and_is_restored(monkeypatch):
    client = _use(monkeypatch, "brief for [name-1]")
    first = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    second = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    assert len(client.sent) == 1 and second.cached
    assert first.text == second.text == "brief for Farhan Siddiqui"


def test_what_is_cached_is_the_tokenised_answer_not_the_borrowers_name(monkeypatch):
    """The cache outlives the request; it should not become a second copy of
    the borrower's data."""
    _use(monkeypatch, "brief for [name-1]")
    llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    stored = json.dumps([v for _, v in llm._get_store()._d.values()])  # type: ignore[attr-defined]
    assert "[name-1]" in stored and "Farhan Siddiqui" not in stored


def test_a_redaction_failure_refuses_the_call_rather_than_sending_it(monkeypatch):
    client = _use(monkeypatch, "never reached")

    def boom(*_a, **_k):
        raise RuntimeError("regex blew up")

    monkeypatch.setattr(llm, "pseudonymise", boom)
    out = llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    assert out.status == llm.INVALID_REQUEST and not out.ai_generated
    assert "redaction failed" in (out.failure_reason or "")
    assert client.sent == []


def test_nothing_in_the_logs_carries_the_values(monkeypatch, caplog):
    import logging
    _use(monkeypatch, "noted")
    with caplog.at_level(logging.DEBUG):
        llm.complete(PROMPT, purpose="visit_strategy", names=["Farhan Siddiqui"])
    blob = caplog.text
    for secret in ("Farhan Siddiqui", "9899000101", "ABCDE1234F"):
        assert secret not in blob


def test_every_call_site_declares_the_names_it_embeds():
    """A tripwire, not a proof. The seam redacts patterns whatever a caller
    does, but only the caller knows which PEOPLE its prompt names, so a new
    complete() that forgets `names=` silently loses that half. Add to
    ALLOWED_WITHOUT_NAMES only for a prompt that embeds no person."""
    import ast
    import pathlib

    ALLOWED_WITHOUT_NAMES: set[str] = set()      # every call site names someone today
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    missing: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        if path.name in ("llm.py", "redaction.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "complete":
                continue
            # llm.complete(...) called directly, or handed to an executor.
            where = f"{path.relative_to(app_dir).as_posix()}:{node.lineno}"
            if where in ALLOWED_WITHOUT_NAMES:
                continue
            if not any(kw.arg == "names" for kw in node.keywords):
                missing.append(where)
    assert missing == [], f"complete() without names=: {missing}"
