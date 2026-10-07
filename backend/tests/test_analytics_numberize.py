"""Regression pin for the Cost-to-Collect white screen (2026-10-07).

`AnalyticsTabOut.panels` is an untyped ``dict``; Postgres ``numeric`` columns
arrive as ``Decimal``, and Pydantic v2 serialises a ``Decimal`` inside an untyped
dict as a JSON *string* ("2.63"). The frontend formats those as numbers
(``.toFixed``), which throws on a string and unmounts the whole bank tree.
``numberize`` converts every Decimal to float so the wire matches the typed
``number`` contract the panels declare.
"""
import json
from decimal import Decimal

from app.api.v1.endpoints.bank import AnalyticsTabOut
from app.services.bank.analytics_catalog import numberize


def test_numberize_converts_nested_decimals_and_leaves_the_rest():
    src = {"by_agency": [{"cost_per_100_inr": Decimal("2.63"),
                          "commission_inr": Decimal("556114.39"),
                          "agency_name": "Sarthak Recovery Services",
                          "placed_new": 5, "field_cost_inr": None}]}
    row = numberize(src)["by_agency"][0]
    assert isinstance(row["cost_per_100_inr"], float) and row["cost_per_100_inr"] == 2.63
    assert isinstance(row["commission_inr"], float)
    assert row["agency_name"] == "Sarthak Recovery Services"   # strings untouched
    assert row["placed_new"] == 5                               # ints untouched
    assert row["field_cost_inr"] is None                        # None untouched


def test_panels_serialize_as_json_numbers_never_strings():
    panels = numberize({"by_month": [{"cost_per_100_inr": Decimal("2.63"),
                                       "collected_inr": Decimal("21154433.77")}]})
    wire = json.loads(AnalyticsTabOut(tab="cost", available=True, reason=None, panels=panels).model_dump_json())
    for key in ("cost_per_100_inr", "collected_inr"):
        v = wire["panels"]["by_month"][0][key]
        assert isinstance(v, (int, float)) and not isinstance(v, str), f"{key} serialized as {v!r}"
