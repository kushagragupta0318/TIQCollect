"""Local retrieval-augmented grounding for the narrative LLM calls
(owner-directed build, 2026-10-07).

Gated by `settings.LLM_RAG_ENABLED`. The corpus (`core/rag/corpus/*.md`) is
chunked and embedded once, lazily, on first `retrieve()` call — never at
import, so importing this module costs nothing, and a test suite that
never calls `retrieve()` never touches the embedding backend at all.

HARD DEGRADE RULE (owner-directed, local-first — "degrades vs breaks"):
if the embedding package cannot be imported, the model cannot be loaded or
downloaded, or anything else goes wrong building the index, `retrieve()`
returns `[]` and logs once. It never raises and never blocks the LLM call
it was meant to ground. RAG is additive grounding, never a boot
dependency — with the backend not yet installed (this build's first
phase), `retrieve()` already exercises exactly this path, which is also
what pins it in tests/test_rag.py without needing the heavy dependency
installed at all.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import structlog

from app.core.config import settings

logger = structlog.get_logger()

CORPUS_DIR = pathlib.Path(__file__).parent / "corpus"
# fastembed resolves this name to one fixed model snapshot from its own
# registry; the `fastembed` package version itself (requirements.txt, once
# added) is the reproducibility pin.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
# A chunk is a markdown "## " section; the heading is kept as the chunk's
# own first line (context for both the embedding and the model reading it
# back) and as half of its `source` id.
_HEADING = "\n## "


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str          # "<corpus file stem>#<heading>"


def load_corpus(corpus_dir: pathlib.Path = CORPUS_DIR) -> list[Chunk]:
    """Pure: split every `.md` file in the corpus directory into its
    level-2-heading sections. No embedding, no network — safe to call with
    no heavy dependency installed, which is exactly how tests/test_rag.py
    checks the corpus itself independently of the embedding backend."""
    chunks: list[Chunk] = []
    for path in sorted(corpus_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for i, section in enumerate(text.split(_HEADING)):
            body = section.strip() if i == 0 else ("## " + section).strip()
            if not body:
                continue
            heading = body.splitlines()[0].lstrip("# ").strip()
            chunks.append(Chunk(text=body, source=f"{path.stem}#{heading}"))
    return chunks


_STATE: dict | None = None   # {} once built with nothing usable; {"chunks", "vectors", "model"} otherwise


def _load() -> dict:
    """Builds the index exactly once, lazily. Any failure here — the
    package missing (today's state, deliberately: the build is staged),
    a model download failing, a corrupt cache — is caught and cached as
    "nothing usable" so a flaky dependency does not retry on every call
    and does not surface as an LLM-call failure."""
    global _STATE
    if _STATE is not None:
        return _STATE
    try:
        import numpy as np
        from fastembed import TextEmbedding   # deferred: not a module-level dependency

        chunks = load_corpus()
        if not chunks:
            _STATE = {}
            return _STATE
        model = TextEmbedding(model_name=EMBEDDING_MODEL)
        vectors = np.array(list(model.embed([c.text for c in chunks])))
        _STATE = {"chunks": chunks, "vectors": vectors, "model": model}
    except Exception as exc:                                    # noqa: BLE001 — fail closed, see module docstring
        logger.warning("rag.index_unavailable", error=str(exc), error_type=type(exc).__name__)
        _STATE = {}
    return _STATE


def reset_for_tests() -> None:
    """Drops the cached index/model so a test can force a fresh `_load()`."""
    global _STATE
    _STATE = None


def retrieve(query: str, k: int = 4) -> list[Chunk]:
    """Top-`k` corpus chunks by cosine similarity to `query`. `[]` when RAG
    is off (`settings.LLM_RAG_ENABLED`), the query is empty, the corpus is
    empty, or the embedding backend is unavailable for any reason — see
    the module docstring's hard degrade rule. Never raises."""
    if not settings.LLM_RAG_ENABLED or not query:
        return []
    state = _load()
    if not state:
        return []
    try:
        import numpy as np
        q = np.array(list(state["model"].embed([query])))[0]
        vectors = state["vectors"]
        sims = vectors @ q / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(q) + 1e-9)
        top = np.argsort(-sims)[:k]
        return [state["chunks"][i] for i in top]
    except Exception as exc:                                    # noqa: BLE001 — fail closed, see module docstring
        logger.warning("rag.retrieve_failed", error=str(exc), error_type=type(exc).__name__)
        return []


def reference_block(query: str, k: int = 4) -> str:
    """The fenced "Reference material" prompt block a call site inserts
    between its figures and its instruction — or "" when `retrieve()`
    returns nothing, so a call site can always do
    `prompt + rag.reference_block(query)` with no branching of its own.
    The frame states the HARD RULE inline, at the point the model actually
    reads the material, not only in a docstring a prompt author might
    forget to restate."""
    chunks = retrieve(query, k)
    if not chunks:
        return ""
    body = "\n\n".join(f"[{c.source}]\n{c.text}" for c in chunks)
    return (
        "\n\nReference material (style and conduct guidance only — compute every figure "
        "from the data given above, never from here):\n" + body
    )
