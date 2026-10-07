"""Small eval: what RAG grounding actually changes, for a couple of the real
narrative purposes (owner-directed RAG + guardrails build, 2026-10-07).

Default mode needs nothing — no API key, no DB, no network beyond the
embedding model's own cache (already warm after the first `rag.retrieve()`
call anywhere): it shows the exact reference-material block each purpose's
prompt gains, retrieved live against the real corpus, so a reviewer can see
what grounding was actually injected rather than take it on faith.

`--live` additionally runs a real llm.complete() call for each purpose,
once without the reference block and once with it, so the two narratives
can be read side by side. It needs a configured provider (LLM_PROVIDER +
a key) — this script never invents one; with none configured it says so
and still runs the no-API part.

    python -m scripts.eval_rag_grounding
    python -m scripts.eval_rag_grounding --live
"""
from __future__ import annotations

import argparse
import sys

from app.core import llm, rag

# Two sample cases, close to what the real call sites build (agent.py's
# visit_strategy, manager.py's agent_insight) but hand-authored so this
# script needs no DB at all.
_CASES = [
    {
        "purpose": "visit_strategy",
        "query": "visit approach and tone for a borrower in the FIELD stage, BUCKET_2 DPD bucket",
        "figures": (
            "Customer: Ramesh Patil (SALARIED, Pune)\n"
            "Loan: PERSONAL with Girivan Finance Ltd\n"
            "DPD: 45 days overdue | Bucket: BUCKET_2\n"
            "Total outstanding: Rs 1,85,000 | Overdue: Rs 32,000\n"
            "Case target: Rs 32,000 | Collected so far: Rs 0\n"
            "Visit 2 of 3 allowed. No call logs — no pre-visit calls recorded."
        ),
        "instruction": (
            "Write a 2-3 sentence visit strategy: recommended tone and approach for the field agent. "
            "Use ONLY the figures given above; never invent a number."
        ),
    },
    {
        "purpose": "agent_insight",
        "query": "coaching guidance for a field agent's performance and conduct",
        "figures": (
            "Agent: Suresh Kamble, Tier 2, Pune territory\n"
            "Collection rate: 38% (team avg: 52%) | PTP honor rate: 61% (team avg: 70%)\n"
            "Per-visit yield: Rs 1,850 (team avg: Rs 2,600) | Active cases: 22 | Resolved: 9"
        ),
        "instruction": (
            "Write a 2-3 sentence coaching insight and one recommended action for the manager. "
            "Use ONLY the figures given above; never invent a number."
        ),
    },
]


def _prompt(case: dict, *, grounded: bool) -> str:
    block = rag.reference_block(case["query"]) if grounded else ""
    return f"{case['figures']}\n\n{case['instruction']}{block}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="also run a real llm.complete() call, ungrounded vs grounded")
    args = ap.parse_args()

    for case in _CASES:
        print(f"\n{'=' * 70}\n{case['purpose']}\n{'=' * 70}")
        block = rag.reference_block(case["query"])
        if not block:
            print("(no grounding retrieved -- RAG off, or the embedding backend is unavailable)")
            continue
        print("Reference material retrieved:")
        print(block.strip())

        if not args.live:
            continue
        health = llm.health()
        if not health["usable"]:
            print(f"\n--live requested but no provider is usable ({health['unusable_reason']}); skipping the call.")
            continue
        ungrounded = llm.complete(_prompt(case, grounded=False), purpose=case["purpose"], max_tokens=300)
        grounded = llm.complete(_prompt(case, grounded=True), purpose=case["purpose"], max_tokens=300)
        print(f"\nUNGROUNDED ({ungrounded.status}):\n{ungrounded.text or '(no text)'}")
        print(f"\nGROUNDED ({grounded.status}):\n{grounded.text or '(no text)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
