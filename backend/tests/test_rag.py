"""core/rag — the corpus loader, retrieve()'s ranking math, and the hard
degrade rule for when the embedding backend is unavailable (owner-directed
RAG build, 2026-10-07).

fastembed is installed, but this suite blocks all network access
(tests/conftest.py's own rule — "no test reaches the network"), so a real
embed call cannot run here even on a machine that has the package: a cold
model cache tries to fetch from Hugging Face and is refused. retrieve()'s
cosine-similarity ranking is still fully tested, deterministically and
offline, by faking only the query embedding (see
test_retrieve_ranks_by_cosine_similarity_and_respects_k) — what cannot be
unit-tested here is MODEL QUALITY (does this query actually retrieve the
right corpus section), which is a fuzzy claim about the embedding model,
not about this module's code; that is verified by hand against the live
corpus in scripts/eval_rag_grounding.py, with its output recorded in the
build's commit message.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

from app.core import rag
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset_rag_index():
    rag.reset_for_tests()
    yield
    rag.reset_for_tests()


def test_the_corpus_has_every_expected_document():
    chunks = rag.load_corpus()
    sources = {c.source.split("#")[0] for c in chunks}
    assert sources == {
        "collections_playbook", "rbi_fair_practices", "ptp_visit_best_practice", "metric_definitions",
    }


def test_every_chunk_is_non_empty_and_carries_its_heading():
    chunks = rag.load_corpus()
    assert len(chunks) >= 8   # several "## " sections per doc, four docs
    for c in chunks:
        assert c.text.strip()
        assert c.source and "#" in c.source


def test_a_doc_s_own_title_section_is_kept_as_a_chunk():
    chunks = rag.load_corpus()
    titles = [c for c in chunks if c.source.startswith("metric_definitions#")]
    assert any("TIQCollect metric definitions" in c.text for c in titles)


def test_retrieve_ranks_by_cosine_similarity_and_respects_k(monkeypatch):
    # The test suite blocks all network access (tests/conftest.py), so a real
    # fastembed call cannot run here even with the package installed -- a
    # cold cache tries to fetch the model and is refused, same as the
    # degrade path below. retrieve()'s actual ranking math is still fully
    # testable by faking only the query embedding, offline and
    # deterministic. Real semantic relevance is verified by hand against the
    # live corpus (scripts/eval_rag_grounding.py; see the build's commit for
    # the recorded output), which is exactly the kind of fuzzy, model-quality
    # claim a unit test should not be asserting in the first place.
    chunks = [rag.Chunk(text="a", source="x#a"), rag.Chunk(text="b", source="x#b"),
             rag.Chunk(text="c", source="x#c")]
    vectors = np.array([[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]])   # a and c close; b orthogonal

    class _FakeModel:
        def embed(self, texts):
            return [[1.0, 0.0]] * len(texts)   # the query: closest to a, then c, then b

    monkeypatch.setattr(rag, "_load", lambda: {"chunks": chunks, "vectors": vectors, "model": _FakeModel()})
    assert [c.source for c in rag.retrieve("anything", k=2)] == ["x#a", "x#c"]
    assert [c.source for c in rag.retrieve("anything", k=1)] == ["x#a"]


def test_retrieve_degrades_to_empty_without_raising_when_the_backend_cannot_import(monkeypatch):
    # Force the exact condition the degrade rule exists for, rather than
    # relying on fastembed happening not to be installed -- pins the rule
    # regardless of what this environment has installed.
    real_import = __import__

    def _no_fastembed(name, *a, **k):
        if name == "fastembed" or name.startswith("fastembed."):
            raise ImportError("simulated: fastembed unavailable")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _no_fastembed)
    for mod in list(sys.modules):
        if mod == "fastembed" or mod.startswith("fastembed."):
            monkeypatch.delitem(sys.modules, mod)
    out = rag.retrieve("how should a field agent talk to a delinquent borrower")
    assert out == []


def test_retrieve_is_off_when_the_flag_is_off(monkeypatch):
    monkeypatch.setattr(settings, "LLM_RAG_ENABLED", False)
    assert rag.retrieve("anything") == []


def test_retrieve_is_off_on_an_empty_query():
    assert rag.retrieve("") == []


def test_reference_block_is_empty_when_retrieve_has_nothing():
    # retrieve()'s own degrade/off paths, not "no chunk was similar enough" --
    # cosine top-k over a non-empty corpus always returns something, so an
    # empty reference_block is a property of retrieve() returning [], tested
    # directly rather than hoped for from a query string.
    assert rag.reference_block("") == ""


def test_reference_block_states_the_hard_rule_and_cites_its_sources(monkeypatch):
    monkeypatch.setattr(rag, "retrieve", lambda query, k=4: [rag.Chunk(text="body text", source="doc#heading")])
    block = rag.reference_block("anything")
    assert "compute every figure" in block and "never from here" in block
    assert "doc#heading" in block and "body text" in block
