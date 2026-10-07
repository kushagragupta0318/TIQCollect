"""core/llm.py's usage metering (F11): complete()/chat() write one ai.llm_calls
row per call, best-effort.

Everything else in tests/test_llm.py stubs the client and never configures a
database, because recording must not change behaviour for any of those tests
-- the first two tests here are the other half of that claim: it actually
writes when a database IS there, and it is silent (never raises, never
changes the returned result) when the write fails.
"""
from __future__ import annotations

import pytest

from app.core import llm
from app.core.config import settings
from app.models.llm_call import LLMCall
from tests._db import DEFAULT_TENANT, create_schema, drop_schema, make_engine, make_session_factory

BANK = DEFAULT_TENANT["bank_id"]

engine = make_engine()
TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)


class _Usage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _Msg:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content): self.message = _Msg(content)


class _RespWithUsage:
    def __init__(self, content, prompt_tokens=1000, completion_tokens=200):
        self.choices = [_Choice(content)]
        self.usage = _Usage(prompt_tokens, completion_tokens)


class _FakeCompletions:
    def __init__(self, resp): self._resp = resp

    def create(self, **kwargs):
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.chat = type("C", (), {"completions": _FakeCompletions(resp)})()


@pytest.fixture()
def db(monkeypatch):
    create_schema(engine)
    import app.core.database as database_module
    monkeypatch.setattr(database_module, "SessionLocal", TestingSession)
    s = TestingSession()
    yield s
    s.close()
    drop_schema(engine)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "openai/gpt-oss-120b")   # in the price table
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "")
    monkeypatch.setattr(settings, "LLM_CACHE_TTL_SECONDS", 0)   # a cache hit records no NEW usage
    monkeypatch.setattr(llm, "_store", llm._MemoryStore())
    yield
    llm._store = None


def _use(monkeypatch, resp):
    client = _FakeClient(resp)
    monkeypatch.setattr(llm, "_client", lambda *_a, **_k: client)


def test_a_completion_records_a_usage_row(monkeypatch, db):
    _use(monkeypatch, _RespWithUsage("hello", prompt_tokens=1000, completion_tokens=200))
    r = llm.complete("p", purpose="briefing", bank_id=BANK)
    assert r.status == llm.OK

    rows = db.query(LLMCall).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.bank_id == BANK
    assert row.feature == "briefing"
    assert row.model == "openai/gpt-oss-120b"
    assert row.input_tokens == 1000 and row.output_tokens == 200
    # (0.15 * 1000 + 0.75 * 200) / 1_000_000, the table's own published rate.
    assert row.cost == pytest.approx((0.15 * 1000 + 0.75 * 200) / 1_000_000)


def test_no_bank_id_records_an_unattributed_row(monkeypatch, db):
    """Every call site today omits bank_id -- the known gap services/bank/
    usage_read.py's coverage block states, reproduced at the source."""
    _use(monkeypatch, _RespWithUsage("hello"))
    llm.complete("p", purpose="briefing")
    assert db.query(LLMCall).one().bank_id is None


def test_an_unpriced_model_records_tokens_with_a_null_cost(monkeypatch, db):
    monkeypatch.setattr(settings, "LLM_MODEL", "some-future-model")
    _use(monkeypatch, _RespWithUsage("hello", prompt_tokens=500, completion_tokens=50))
    llm.complete("p", purpose="briefing", bank_id=BANK)
    row = db.query(LLMCall).one()
    assert row.input_tokens == 500
    assert row.cost is None


def test_the_fake_provider_never_writes_a_row(db):
    with llm.use_fake(["hello"]):
        llm.complete("p", purpose="briefing", bank_id=BANK)
    assert db.query(LLMCall).count() == 0


def test_a_recording_failure_never_changes_the_result(monkeypatch, db):
    """Best-effort: a metering row is never the reason a product feature
    fails (the brief's own words for this seam)."""
    import app.core.database as database_module

    def _boom():
        raise RuntimeError("database is down")

    monkeypatch.setattr(database_module, "SessionLocal", _boom)
    _use(monkeypatch, _RespWithUsage("hello"))
    r = llm.complete("p", purpose="briefing", bank_id=BANK)
    assert r.status == llm.OK and r.text == "hello"


def test_a_cached_reply_records_no_new_usage(monkeypatch, db):
    """A cache hit cost nothing new; a row claiming otherwise would double the
    page's totals for a call the provider was never asked to repeat."""
    monkeypatch.setattr(settings, "LLM_CACHE_TTL_SECONDS", 300)
    _use(monkeypatch, _RespWithUsage("hello", prompt_tokens=1000, completion_tokens=200))
    llm.complete("p", purpose="briefing", bank_id=BANK)
    llm.complete("p", purpose="briefing", bank_id=BANK)   # served from cache
    rows = db.query(LLMCall).order_by(LLMCall.created_at).all()
    assert len(rows) == 2
    assert rows[0].input_tokens == 1000
    assert rows[1].input_tokens == 0 and rows[1].cost == 0.0
