"""Product rules the ledger's derived views must obey (2026-09-28).

The simulator is free to generate what a borrower might do; the PANEL (what a
model is trained on) and the MATERIALISER (what the real schema holds) must
both describe only what the product can record, and describe the SAME set, or
the Phase 3 equality harness compares two different worlds. Each rule here
imports the product's own definition rather than restating it.
"""
from __future__ import annotations

import pandas as pd

from app.models.ptp import promise_is_for_money


def product_promises(ptps: pd.DataFrame) -> pd.DataFrame:
    """The ledger's promises the product can hold: a promise is for money.

    The simulator promises min(overdue, ...), which is 0 when nothing is
    overdue. v2's ptps CHECK refuses such a row, so the materialiser could not
    write it, and the panel must not count it either (coordinator decision (a),
    2026-09-28: the product rule wins on both sides).
    """
    if not len(ptps):
        return ptps
    return ptps[ptps["committed_amount"].map(promise_is_for_money)]
