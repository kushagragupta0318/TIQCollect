"""The demo generator's pure parts (app/demo, B16): what can be shown without
a database. The Postgres half — the committed dump's invariants — is
tests/pg/test_pg_demo_fixture.py.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from app.demo import documents as D
from app.demo import roster as R
from app.demo.latent import AGENCY_LATENT, AgencyLatent
from app.ml.simulation.ledger.config import LedgerConfig
from app.ml.simulation.ledger.simulator import LedgerSimulator


def _small(seed=5):
    return replace(LedgerConfig(), n_borrowers=80, months=3, n_agents=6, seed=seed, start_date=date(2026, 3, 1))


def test_a_neutral_latent_reproduces_the_ledger_exactly():
    """The subclass changes the skill draw's mean and spread and NOTHING else:
    with the ledger's own N(0, 0.35) it must produce the identical book, so
    every other table comes from the ledger's untouched logic."""
    from app.demo.books import DemoLedgerSimulator
    base = LedgerSimulator(_small()).run()
    same = DemoLedgerSimulator(_small(), AgencyLatent(skill_mean=0.0, skill_sd=0.35), calendar=False).run()
    for name, frame in base.tables().items():
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), same.tables()[name].reset_index(drop=True),
                                      check_like=False, obj=name)


def test_latent_skill_moves_only_the_agents_and_what_they_cause():
    from app.demo.books import DemoLedgerSimulator
    strong = DemoLedgerSimulator(_small(), AgencyLatent(skill_mean=0.6, skill_sd=0.05)).run()
    weak = DemoLedgerSimulator(_small(), AgencyLatent(skill_mean=-0.6, skill_sd=0.05)).run()
    assert strong.agents.agent_skill.mean() > 0.5 > -0.5 > weak.agents.agent_skill.mean()
    # The opening pool is drawn before any agent acts, so it is identical;
    # the accounts that open LATER replace ones that closed, and closing
    # depends on payment behaviour, which skill is meant to change.
    n = _small().n_borrowers
    pd.testing.assert_frame_equal(strong.borrowers.head(n).reset_index(drop=True),
                                  weak.borrowers.head(n).reset_index(drop=True))


def test_the_ledger_s_default_season_is_its_own_sine_and_the_hook_is_wired():
    """L6 moved the inline term into LedgerSimulator._season. The default
    must be the same expression (the pre/post tables were byte-identical,
    measured on 2026-09-30), and a subclass must actually reach the day loop."""
    import numpy as np
    sim = LedgerSimulator(_small())
    for t in range(0, 400, 7):
        month = t // sim.cfg.cycle_days
        assert sim._season(t, month) == sim.cfg.seasonality_amplitude * np.sin(2 * np.pi * month / 12.0)

    class Flat(LedgerSimulator):
        def _season(self, t, month):
            return -3.0
    assert len(Flat(_small()).run().payments) < len(LedgerSimulator(_small()).run().payments)


def test_the_demo_calendar_lifts_march_and_softens_april_on_the_same_book():
    from app.demo.books import DemoLedgerSimulator
    from app.demo.latent import CALENDAR_SEASON, SALARY_DAYS, calendar_season
    cfg = replace(LedgerConfig(), n_borrowers=600, months=6, n_agents=10, seed=11, start_date=date(2026, 1, 1))
    lat = AgencyLatent(skill_mean=0.0, skill_sd=0.35)

    def by_month(led):
        days = led.payments.payment_day.map(lambda d: (cfg.start_date + timedelta(days=int(d))).month)
        return days.value_counts()
    cal = by_month(DemoLedgerSimulator(cfg, lat).run())
    flat = by_month(DemoLedgerSimulator(cfg, lat, calendar=False).run())
    assert cal[3] / cal[4] > flat[3] / flat[4]
    # roughly zero-mean, so the book's overall payment rate is not what moves
    year = [calendar_season(date(2026, 1, 1) + timedelta(days=k)) for k in range(365)]
    assert abs(sum(year) / len(year)) < 0.03
    assert set(CALENDAR_SEASON) == set(range(1, 13))
    assert [lo for lo, _hi, _v in SALARY_DAYS] == [1, 8, 11] and SALARY_DAYS[-1][1] == 31


def test_the_ledger_config_has_no_generator_fields():
    """The 2.2.0 artifacts record LedgerConfig fingerprints; a field added for
    the generator would change every one. Latent quality lives in the demo
    package, never on the ledger's config."""
    names = set(LedgerConfig.__dataclass_fields__)
    assert not names & {"skill_mean", "skill_sd", "agency", "latent", "spike_rate"}


def test_every_generated_agency_has_a_latent_and_no_latent_names_a_model_metric():
    assert set(AGENCY_LATENT) == {a.key for a in R.GENERATED_AGENCIES if a.n_agents}
    for key, lat in AGENCY_LATENT.items():
        assert not any(k in str(lat.to_dict()).lower() for k in ("gini", "auc", "ks_", "brier")), key


def test_the_generator_refuses_a_non_rbi_contact_window(monkeypatch):
    from app.core.config import settings
    from app.demo.books import require_rbi_window
    monkeypatch.setattr(settings, "CONTACT_HOUR_START", 0)
    monkeypatch.setattr(settings, "CONTACT_HOUR_END", 24)
    with pytest.raises(RuntimeError, match="RBI"):
        require_rbi_window()


# ── specimen documents ──────────────────────────────────────────────────────
def test_specimens_are_valid_deterministic_pdfs_with_the_footer():
    a, b = D.specimens(R.SAHYADRI), D.specimens(R.SAHYADRI)
    assert [s.sha256 for s in a] == [s.sha256 for s in b]                       # stable across runs
    for s in a:
        assert s.body.startswith(b"%PDF-1.4") and s.body.rstrip().endswith(b"%%EOF")
        assert "Specimen — fictional demo document".encode("cp1252") in s.body
        assert b"CreationDate" not in s.body                                    # no clock inside
        assert s.storage_key == f"agency-documents/{R.SAHYADRI.id}/{s.doc_type.lower()}.pdf"
    # xref offsets point at their objects
    body = a[0].body
    xref = int(body.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert body[xref:xref + 4] == b"xref"


def test_document_story_is_the_roster_s():
    by_type = {s.doc_type: s for s in D.specimens(R.SAHYADRI)}
    assert by_type["INSURANCE"].expires_on == R.ANCHOR_DATE + timedelta(days=21)
    hooghly = {s.doc_type for s in D.specimens(R.HOOGHLY)}
    assert len(hooghly) == 5 and not hooghly & set(R.HOOGHLY.missing_docs)
    assert all(s.issued_on <= R.ANCHOR_DATE for a in R.AGENCIES for s in D.specimens(a))


def test_plus_years_survives_a_leap_day():
    assert R.plus_years(date(2024, 2, 29), 3) == date(2027, 2, 28)
    assert R.plus_years(date(2024, 3, 1), 3) == date(2027, 3, 1)


def test_the_driver_profiles():
    from scripts.generate_demo_v2 import PROFILES
    assert set(PROFILES) == {"dev", "demo", "stress"}
    assert set(PROFILES["demo"].agencies) == {a.key for a in R.AGENCIES}
    assert PROFILES["dev"].agents_cap and PROFILES["stress"].agents_scale > 1


def test_contact_hours_decide_the_flag_the_way_the_product_does():
    """The generator derives within_contact_hours with the product's own
    function; pin its edges so a change there is seen here."""
    from app.core.geo import is_within_contact_hours
    ist = timezone(timedelta(hours=5, minutes=30))
    assert is_within_contact_hours(datetime(2026, 9, 1, 8, 0, tzinfo=ist))
    assert is_within_contact_hours(datetime(2026, 9, 1, 18, 59, tzinfo=ist))
    assert not is_within_contact_hours(datetime(2026, 9, 1, 19, 0, tzinfo=ist))
    assert not is_within_contact_hours(datetime(2026, 9, 1, 7, 59, tzinfo=ist))


def test_the_open_invite_can_never_be_redeemed():
    """Audit HIGH: the placeholder invite's token hash must not be computable
    from anything in the source. Random, discarded preimage: two calls differ."""
    from app.demo.world import unredeemable_token_hash
    a, b = unredeemable_token_hash(), unredeemable_token_hash()
    assert a != b and len(a) == 64 and int(a, 16) >= 0
