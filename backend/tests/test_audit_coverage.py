"""The audit-coverage scan: that it tells a WRITE from a READ, and says so.

`coverage.not_instrumented` is served on the manager Compliance tab, where it
answers "is this action missing because the week was quiet, or because nothing
records it?". It replaced a hand-written list that had gone wrong in the worst
direction -- declaring VISIT_RECORDED and six others never recorded while they
had live write sites -- so the scan that replaced it has to be pinned against
exactly that failure, in both directions.
"""
from __future__ import annotations

import ast

from app.core import audit_coverage as ac
from app.models.audit_log import AuditAction


def _scan(source: str) -> tuple[set[str], set[str]]:
    """Run the scanner's own logic over a snippet, the way it runs over app/."""
    written: set[str] = set()
    unclassified: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = ac._callee_name(node)
        for kw in node.keywords:
            if kw.arg == "action" and (n := ac._action_of(kw.value)):
                written.add(n)
        for arg in node.args:
            if not (n := ac._action_of(arg)):
                continue
            if callee in ac.WRITER_NAMES:
                written.add(n)
            elif callee not in ac.READER_NAMES:
                unclassified.add(callee)
    return written, unclassified


def test_a_write_counts_and_a_filter_does_not():
    """The distinction the old grep could not make, and the reason the list
    was hand-maintained: manager.py READS CONTACT_HOUR_VIOLATION_ATTEMPT in a
    filter and does not write it there."""
    written, _ = _scan("""
write_audit(db, action=AuditAction.LOGIN, user_id=u)
stage_audit(db, action=AuditAction.LOGOUT, user_id=u)
db.add(AuditLog(action=AuditAction.MODEL_PROMOTED, user_id=u))
_log(db, AuditAction.TOKEN_REFRESH, u, request)
q.filter(AuditLog.action == AuditAction.PAYMENT_VERIFIED)
q.filter(AuditLog.action.in_([AuditAction.PTP_SET]))
""")
    assert written == {"LOGIN", "LOGOUT", "MODEL_PROMOTED", "TOKEN_REFRESH"}


def test_an_unrecognised_wrapper_is_reported_not_guessed():
    """How PAYMENT_REVERSED was nearly reported as never recorded: the reversal
    service writes through its own `self._audit(...)`. A call the scanner does
    not recognise must surface, so the answer is never quietly wrong."""
    written, unclassified = _scan("self._not_a_known_writer(AuditAction.PAYMENT_REVERSED, user)")
    assert written == set()
    assert unclassified == {"_not_a_known_writer"}


def test_the_real_scan_classifies_every_call_site_in_the_app():
    """The tripwire that matters day to day: a new audit wrapper fails this
    test instead of silently removing its actions from the coverage figure."""
    c = ac.coverage()
    assert c.unclassified == (), (
        f"{list(c.unclassified)}: handed an AuditAction and recognised as neither a writer nor a "
        f"reader. Add it to audit_coverage.WRITER_NAMES if it writes a row, or to READER_NAMES."
    )


def test_the_real_scan_found_the_write_sites():
    """A scan that could not read app/ would find nothing and report every
    action as instrumented -- the comfortable direction to be wrong in. 83
    sites on 2026-10-07; the floor is loose on purpose, it is here to catch a
    scan that found NOTHING."""
    c = ac.coverage()
    assert c.write_sites > 50, c.write_sites


def test_the_actions_the_old_hand_list_got_wrong_are_instrumented():
    """Named one by one, because this is the bug: on 2026-10-07 the Compliance
    tab declared all nine of these never recorded, and each has a live write
    site. VISIT_RECORDED on an RBI screen is the one that matters -- it told a
    lender that field visits are not logged."""
    instrumented = set(a.name for a in AuditAction) - set(ac.coverage().not_instrumented)
    for name in ("VISIT_RECORDED", "PTP_SET", "PAYMENT_SUBMITTED", "CASE_ASSIGNED",
                 "DATA_EXPORT", "ROLE_VIOLATION_ATTEMPT", "AGENT_STATUS_CHANGED",
                 "DOCUMENT_UPLOADED", "CONTACT_HOUR_VIOLATION_ATTEMPT"):
        assert name in instrumented, name


def test_an_action_only_a_demo_book_writes_is_not_instrumented():
    """demo/world.py writes AGENCY_SUSPENDED when it builds a book. A demo
    fixture writing a row is not the product recording one, and a lender
    reading this list is asking about the product."""
    assert "AGENCY_SUSPENDED" in ac.coverage().not_instrumented


def test_every_reported_name_is_a_declared_action():
    declared = {a.name for a in AuditAction}
    assert set(ac.coverage().not_instrumented) <= declared
