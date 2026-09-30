"""With OSRM_BASE_URL empty (the default since 2026-09-28) routing makes no
network call at all: straight-line estimates, never the public demo server."""
from __future__ import annotations

from app.core import routing

COORDS = [(28.4595, 77.0266), (28.47, 77.03), (28.48, 77.05)]


def test_no_osrm_means_no_call_and_haversine(monkeypatch):
    monkeypatch.setattr(routing, "_OSRM_BASE", "")
    def boom(*a, **k):
        raise AssertionError("routing must not call out when OSRM is not configured")
    monkeypatch.setattr(routing.httpx, "get", boom)
    table = routing.fetch_osrm_table(COORDS)
    assert table.source == routing.SOURCE_HAVERSINE and len(table.durations) == 3
    geometry, legs, source = routing.fetch_osrm_route(COORDS)
    assert (geometry, source, len(legs)) == (None, routing.SOURCE_HAVERSINE, 2)


def test_the_default_is_empty():
    from app.core.config import Settings
    assert Settings.model_fields["OSRM_BASE_URL"].default == ""
