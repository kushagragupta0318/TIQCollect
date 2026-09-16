"""What the SERVING recovery_risk champion is, for fixtures that must describe
it rather than a version pinned in a literal. 2026-09-16.

Until 2.2.0 was promoted, four test files carried `SERVING = "1.1.0"` and
wrote prediction rows with 1.1.0's four features. The 19:15 task, the health
endpoint and `monitor_model(version=None)` all resolve the champion through
`champion.txt`, so the day the pointer moved, every one of those fixtures
described a version nobody was serving and the monitor correctly said
`not_ready`. Nothing was wrong except the literal.

`served_vector(i)` gives one deterministic synthetic value per feature the
champion SELECTED — the four 1.1.0 inputs keep the values the fixtures always
had, so their arithmetic is unchanged under 1.1.0 — and a complete vector is
what "healthy" means to the stability block; a shorter one is the
missing-feature FINDING, tested on its own.
"""
from __future__ import annotations

import json

from app.ml.pipeline import registry

MODEL = "recovery_risk"
FALLBACK = ["dpd", "cibil_score", "ptp_kept_ratio", "overdue_amount"]


def serving_version(default: str = "1.1.0") -> str:
    return registry.pointer_version(MODEL) or default


def champion_selected() -> list[str]:
    try:
        v = registry.resolve_version(MODEL)
        meta = json.loads((registry.version_dir(MODEL, v) / "metadata.json").read_text())
        return list(meta["selected_features"])
    except Exception:                                   # pragma: no cover
        return list(FALLBACK)


_KNOWN = {
    "dpd": lambda i: float(i % 180),
    "cibil_score": lambda i: 300.0 + (i % 500),
    "ptp_kept_ratio": lambda i: (i % 10) / 10.0,
    "overdue_amount": lambda i: 1000.0 * (1 + i % 20),
}
_LEVELS = {
    "latest_disposition": ["NONE", "WILL_PAY", "REFUSES"],
    "last_commit_status": ["NONE", "KEPT", "OPEN"],
    "recent_ptp_status": ["NONE", "HONORED", "BROKEN"],
    "employment_type": ["SALARIED", "SELF_EMPLOYED"],
    "disposition_recency_class": ["NONE", "WILL_PAY_FRESH", "REFUSES_STALE"],
}


def served_vector(i: int = 0, **fixed) -> dict:
    """Every champion-selected feature, deterministic in `i`; `fixed` pins
    values a test cares about (e.g. dpd=40.0) and wins over the generator."""
    out = {}
    for f in champion_selected():
        if f in fixed:
            out[f] = fixed[f]
        elif f in _KNOWN:
            out[f] = _KNOWN[f](i)
        elif f in _LEVELS:
            out[f] = _LEVELS[f][i % len(_LEVELS[f])]
        else:
            out[f] = float((i * 7) % 23)
    for k, v in fixed.items():
        out.setdefault(k, v)
    return out
