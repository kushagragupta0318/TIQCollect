"""The committed demo fixture, as files (B18). The database half — restore and
check every invariant — is tests/pg/test_pg_demo_fixture.py; these run
everywhere and need nothing but the checkout.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DUMP = FIXTURES / "fieldops-demo-v2.dump"
MANIFEST = FIXTURES / "fieldops-demo-v2.truth.json"
README = FIXTURES / "README.md"


def test_the_v1_csv_copy_is_gone():
    """FX-1 / FX-2: a second, undocumented copy of the v1 book carrying the
    published passwords' hashes and refresh-token hashes. Deleted in B18; it
    remains in git history (d158d95), which the board records."""
    assert not (FIXTURES / "tables").exists() or not list((FIXTURES / "tables").glob("*.csv"))


def test_the_dump_is_a_custom_format_dump_within_the_size_budget():
    head = DUMP.read_bytes()[:5]
    assert head == b"PGDMP"
    assert DUMP.stat().st_size <= 60 * 1024 * 1024          # agreed with the coordinator, 2026-09-28


def test_the_readme_names_the_dump_it_describes():
    digest = hashlib.sha256(DUMP.read_bytes()).hexdigest()
    readme = README.read_text(encoding="utf-8")
    m = re.search(r"fieldops-demo-v2\.dump.*?sha256[^`]*`([0-9a-f]{16})", readme, re.S)
    assert m, "the README's v2 table must quote the dump's sha256 (first 16)"
    assert digest.startswith(m.group(1))
    assert "fictional" in readme.lower()


def test_the_manifest_is_generator_truth_for_every_generated_agency():
    from app.demo.latent import AGENCY_LATENT
    truth = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert truth["profile"] == "demo" and "SYNTHETIC_WARNING" in truth
    assert set(truth["agencies"]) == set(AGENCY_LATENT)
    for key, t in truth["agencies"].items():
        assert t["latent"] == AGENCY_LATENT[key].to_dict(), key
        assert set(t["agent_gender"].values()) <= {"MALE", "FEMALE"}
