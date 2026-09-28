"""One definition of the customer's DECEASED tag (coordinator, 2026-09-28).

visit_service writes it, ml/pipeline/outcomes censors on it, and the ledger
materialiser writes it for a simulated death. They spelled it out separately;
a fourth copy that drifted ("Deceased", "DECEASED ") would leave a deceased
borrower's predictions labelled as unpaid debt. The string "DECEASED" is still
legitimately a VisitOutcome member, an outcome label and a simulator event —
different things with the same spelling — so the allowlist below names each
file and why, and anything else fails.
"""
from __future__ import annotations

import ast
import pathlib

from app.models.customer import CUSTOMER_TAG_DECEASED

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# file -> why "DECEASED" appears there, and it is not the customer tag
ALLOWED = {
    "models/customer.py": "the definition, CUSTOMER_TAG_DECEASED",
    "models/visit.py": "VisitOutcome.DECEASED, the visit outcome",
    "models/repayment_snapshot.py": "OUTCOME_DECEASED, a repayment outcome label",
    "ml/recovery_validation.py": "the censoring outcome labels",
    "ml/simulation/ledger/simulator.py": "the simulator's death EVENT name",
    "ml/simulation/ledger/materialise.py": "comparing that simulator EVENT name",
    "services/ai_report_service.py": "the VisitOutcome -> wording map key",
}


def _literals() -> dict[str, int]:
    found = {}
    for f in sorted(APP.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        n = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == "DECEASED")
        if n:
            found[f.relative_to(APP).as_posix()] = n
    return found


def test_the_tag_is_defined_once_and_nothing_else_spells_it():
    found = _literals()
    assert set(found) <= set(ALLOWED), {k: v for k, v in found.items() if k not in ALLOWED}
    assert all(n == 1 for n in found.values()), found


def test_every_writer_and_reader_uses_the_constant():
    """The literal count cannot see a drifted spelling ("Deceased"); this can:
    each of the three sites must name CUSTOMER_TAG_DECEASED."""
    for rel in ("services/visit_service.py", "ml/pipeline/outcomes.py", "ml/simulation/ledger/materialise.py"):
        tree = ast.parse((APP / rel).read_text(encoding="utf-8"))
        uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "CUSTOMER_TAG_DECEASED"]
        assert uses, f"{rel} no longer uses CUSTOMER_TAG_DECEASED"


def test_the_censor_reads_the_tag_the_visit_writes():
    """Behaviour: a customer carrying the constant is censored as deceased;
    one carrying any other spelling is not."""
    from types import SimpleNamespace
    from app.ml.pipeline import outcomes
    from app.models.case import CaseStatus
    case = SimpleNamespace(status=CaseStatus.CLOSED, resolution_notes="")
    tagged = SimpleNamespace(tags=[CUSTOMER_TAG_DECEASED])
    assert outcomes.censoring_status(case, None, tagged) == outcomes.OutcomeStatus.CENSORED_DECEASED
    for other in (["Deceased"], ["DECEASED "], []):
        assert outcomes.censoring_status(case, None, SimpleNamespace(tags=other)) != \
            outcomes.OutcomeStatus.CENSORED_DECEASED
