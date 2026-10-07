"""Which declared audit actions NOTHING in the product writes.

Served on the manager Compliance tab as `coverage.not_instrumented`, where it
answers the question an auditor actually has about a short log: is this action
missing because the week was quiet, or because nothing records it at all?

It used to be a hand-maintained list, and on 2026-10-07 it was measured wrong
in the worst direction: it named VISIT_RECORDED, PTP_SET, PAYMENT_SUBMITTED,
CASE_ASSIGNED, DATA_EXPORT, ROLE_VIOLATION_ATTEMPT and AGENT_STATUS_CHANGED as
never recorded while every one of them had a live write site. A compliance
screen telling a lender that visits are not logged, when they are, is worse
than the panel not existing.

WHY A SOURCE SCAN IS SOUND HERE, given that manager.py's `_audit_action_coverage`
argues against one. That argument is right and this does not contradict it: it
says a REFERENCE to AuditAction.X is not a WRITE of X, and the proof is in that
very module, which reads CONTACT_HOUR_VIOLATION_ATTEMPT in a filter and writes
nothing. A grep cannot tell those apart. This does not grep -- it parses, and
counts a reference only where it is actually handed to an audit writer:

    write_audit(db, action=AuditAction.X, ...)     -> a write
    stage_audit(db, action=AuditAction.X, ...)     -> a write
    AuditLog(action=AuditAction.X, ...)            -> a write
    _log(db, AuditAction.X, ...)                   -> a write (a local wrapper)
    AuditLog.action == AuditAction.X               -> NOT a write
    AuditLog.action.in_([AuditAction.X])           -> NOT a write

`_audit_action_coverage` stays as it is. It answers a different question --
what this DATABASE has ever recorded -- and remains the right figure for it.

WHAT THIS DELIBERATELY DOES NOT COUNT:
  * app/demo/** -- a demo book writing an action does not mean the product
    does. CONTACT_HOUR_VIOLATION_ATTEMPT is written only by demo/books.py, so
    it is correctly reported as not instrumented.
  * models/audit_log.py -- the declaration itself.
A write site that is dead code still counts as instrumented: whether a path
runs is not something a parser can know, and over-reporting instrumentation is
the direction that gets a surface caught, not the direction that misleads.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models.audit_log import AuditAction

#: Callables that WRITE an audit row. A positional AuditAction handed to one of
#: these is a write; `action=` as a keyword is a write whoever the callee is,
#: because that keyword means nothing else anywhere in this codebase.
#: `_log` (auth_service) and `_audit` (PaymentReversalService) are local
#: wrappers that forward their first positional straight into an AuditLog --
#: the `unclassified` tripwire below is what found `_audit`, rather than
#: PAYMENT_REVERSED and PAYMENT_REVERSAL_REQUESTED being quietly reported as
#: never recorded.
WRITER_NAMES = frozenset({"write_audit", "stage_audit", "AuditLog", "_log", "_audit"})

#: Callables that READ an action -- filters, counts, comparisons. Named so the
#: scanner can tell "I know this is not a write" from "I do not recognise this
#: call at all", which is the case a new audit wrapper would land in.
READER_NAMES = frozenset({
    "filter", "filter_by", "in_", "notin_", "where", "having", "count",
    "label", "case", "any_", "all_", "coalesce", "distinct", "order_by",
    "group_by", "union", "isnot", "is_", "contains", "startswith",
})

_APP = Path(__file__).resolve().parent.parent          # app/
_SKIP_DIRS = ("demo",)
_SKIP_FILES = ("models/audit_log.py",)


@dataclass(frozen=True)
class Coverage:
    """`not_instrumented` is the answer; the other two make it checkable.

    `write_sites` is carried so a caller can tell a real empty answer from a
    scan that found nothing because it could not read the source -- a list
    that says "everything is instrumented" is exactly the comfortable lie this
    module exists to stop telling.
    """
    not_instrumented: tuple[str, ...]
    write_sites: int
    #: Calls that were handed an AuditAction and are neither a known writer
    #: nor a known reader. Empty today; a new audit wrapper puts its name here,
    #: and a test fails rather than the wrapper's actions being miscounted.
    unclassified: tuple[str, ...]


def _callee_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _action_of(node: ast.expr) -> str | None:
    """`AuditAction.X` -> "X", anything else -> None."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)             and node.value.id == "AuditAction":
        return node.attr
    return None


def _scan_file(path: Path, written: set[str], unclassified: set[str]) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return 0        # a file we cannot parse contributes nothing, and says so
                        # through write_sites rather than through a wrong answer
    sites = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee_name(node)
        for kw in node.keywords:
            if kw.arg == "action" and (name := _action_of(kw.value)):
                written.add(name)
                sites += 1
        for arg in node.args:
            if not (name := _action_of(arg)):
                continue
            if callee in WRITER_NAMES:
                written.add(name)
                sites += 1
            elif callee not in READER_NAMES:
                unclassified.add(callee or "<expr>")
    return sites


@lru_cache(maxsize=1)
def coverage() -> Coverage:
    """Scan app/ once per process. ~200 files; the result never changes at
    runtime, because the source does not."""
    written: set[str] = set()
    unclassified: set[str] = set()
    sites = 0
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        if rel.startswith(_SKIP_DIRS) or rel in _SKIP_FILES:
            continue
        sites += _scan_file(path, written, unclassified)
    declared = [a.name for a in AuditAction]
    return Coverage(
        not_instrumented=tuple(sorted(a for a in declared if a not in written)),
        write_sites=sites,
        unclassified=tuple(sorted(unclassified)),
    )
