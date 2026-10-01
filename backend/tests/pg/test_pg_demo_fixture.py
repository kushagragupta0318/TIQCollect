"""The committed v2 demo fixture (backend/fixtures/fieldops-demo-v2.dump,
B16-B18): restored into a scratch database and held to what the fixture
README and the roster promise.

Needs TIQ_PG_TEST_URL (the whole tests/pg suite does) and the pg_restore and
psql client tools; skipped, and says so, without them. The restore mirrors
docker-entrypoint.sh: `pg_restore -f -` piped through psql, because a newer
pg_restore emits SET transaction_timeout, which a 16 server rejects.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.pg.conftest import BACKEND, drop_database, new_database, run_alembic

DUMP = BACKEND / "fixtures" / "fieldops-demo-v2.dump"
MANIFEST = BACKEND / "fixtures" / "fieldops-demo-v2.truth.json"
TOOLS = shutil.which("pg_restore") and shutil.which("psql")

pytestmark = pytest.mark.skipif(not (TOOLS and DUMP.exists()),
                                reason="pg_restore/psql not on PATH, or the v2 dump is absent")

PLACEHOLDERS = [r"ABC (Bank|Collections)", r"Test Bank", r"Synthetic Bank", r"manager1@", r"agent0\d\d@",
                r"Meridian Trust", r"Northfield"]


def _libpq(url: str) -> tuple[list[str], dict]:
    u = make_url(url)
    env = {**os.environ, "PGPASSWORD": u.password or ""}
    return ["-h", u.host or "localhost", "-p", str(u.port or 5432), "-U", u.username or "postgres",
            "-d", u.database], env


@pytest.fixture(scope="module")
def sql_text() -> str:
    """The dump as SQL text: what 'a grep of the fixture' reads."""
    out = subprocess.run(["pg_restore", "--no-owner", "--no-acl", "-f", "-", str(DUMP)],
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8", errors="replace")


@pytest.fixture(scope="module")
def db(sql_text):
    url = new_database("fixture")
    try:
        args, env = _libpq(url)
        body = "\n".join(line for line in sql_text.splitlines() if not line.startswith("SET transaction_timeout"))
        subprocess.run(["psql", *args, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-o", os.devnull],
                       input=f"BEGIN;\n{body}\nCOMMIT;\n", text=True, env=env, check=True, capture_output=True)
        # Restore THEN upgrade, as docker-entrypoint.sh does. The dump is a
        # snapshot at one revision (v2_0016 today), so without this the suite
        # tests today's code against an older schema — which is how the
        # pre-v2_0017 unindexed v_visit_to_pay stayed live in CI and hung the
        # bank-overview test for six hours.
        from alembic import command
        run_alembic(url, command.upgrade, "head")
        engine = create_engine(url)
        # ANALYZE, as docker-entrypoint.sh does: pg_restore loads rows, not
        # pg_statistic, and planned on guesses the bank Overview's
        # v_visit_to_pay join takes 300s+ against 1s analysed. That, not the
        # view or the data, is what hit the 60s statement_timeout.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text("ANALYZE"))
        yield engine
        engine.dispose()
    finally:
        drop_database(url)


def one(db, sql, **kw):
    with db.connect() as c:
        return c.execute(text(sql), kw).scalar()


def rows(db, sql, **kw):
    with db.connect() as c:
        return c.execute(text(sql), kw).all()


# ── B18's done-when: no placeholder anywhere in the fixture ─────────────────
@pytest.mark.parametrize("pattern", PLACEHOLDERS)
def test_a_grep_of_the_fixture_finds_no_placeholder(sql_text, pattern):
    hits = re.findall(pattern, sql_text)
    assert not hits, f"{pattern!r} occurs {len(hits)} times in the dump"


# ── tenancy and people ──────────────────────────────────────────────────────
def test_the_roster_is_what_appendix_c_says(db):
    from app.demo import roster as R
    assert one(db, "select count(*) from tenancy.banks where not is_demo") == 0
    assert one(db, "select count(*) from tenancy.agencies where not is_demo") == 0
    assert {r[0] for r in rows(db, "select legal_name from tenancy.banks")} == {"Girivan Finance Ltd",
                                                                               "Kumaon Finance Ltd"}
    status = Counter(r[0] for r in rows(db, "select status from tenancy.agencies"))
    assert status == {"ACTIVE": 8, "SUSPENDED": 1, "PENDING": 1}
    got = {r[0]: r[1] for r in rows(db, "select a.code, count(g.id) from tenancy.agencies a "
                                        "left join workforce.agents g on g.agency_id = a.id group by a.code")}
    assert got == {a.row["code"]: a.n_agents for a in R.AGENCIES}
    assert one(db, "select count(*) from workforce.agents where gender is null") == 0
    assert one(db, "select count(*) from workforce.agents where gender = 'FEMALE'") > 0


def test_no_usable_password_and_every_login_on_a_demo_domain(db):
    from app.core.config import settings
    from app.core.security import is_disabled_password_hash
    from scripts.apply_demo_logins import FIXTURE_USER_COUNT, REQUIRED_ROLE_GROUPS
    users = rows(db, "select email, hashed_password, role from tenancy.users")
    assert len(users) == FIXTURE_USER_COUNT
    assert all(is_disabled_password_hash(h) for _e, h, _r in users)
    domains = set(settings.DEMO_EMAIL_DOMAINS.split(","))
    assert all(e.rsplit("@", 1)[1] in domains for e, _h, _r in users)
    assert one(db, "select count(*) from tenancy.user_sessions") == 0
    role = {e: r for e, _h, r in users}
    from app.demo.roster import MASTER_ACCOUNTS
    assert len(MASTER_ACCOUNTS) == len(REQUIRED_ROLE_GROUPS)
    for m, (_n, rs) in zip(MASTER_ACCOUNTS, REQUIRED_ROLE_GROUPS):   # positional slots
        assert role[m] in {x.value for x in rs}, m


def test_the_documents_match_the_roster_specimens(db):
    from scripts.ensure_demo_documents import plan
    with db.connect() as c:
        wanted, drift = plan(c)
    assert not drift and len(wanted) == one(db, "select count(*) from tenancy.agency_documents")
    assert one(db, "select count(*) from tenancy.agency_documents where expires_on < date '2026-09-22'") == 0


# ── Aravalli: B15's history, kept exactly (a superset of B15's fixture) ─────
def test_aravalli_history_is_b15s(db):
    from app.demo.roster import AGENCY
    counts = {t: one(db, f"select count(*) from collections.{t} where agency_id = :a", a=AGENCY["id"])
              for t in ("cases", "placements", "visits", "payments", "ptps", "call_logs")}
    assert counts == {"cases": 1698, "placements": 1371, "visits": 2400, "payments": 1036, "ptps": 663,
                      "call_logs": 1089}
    assert one(db, "select count(*) from planning.allocation_decisions") == 78809


# ── derived columns are derived: the product's own rules on every visit ─────
def test_every_visit_flag_is_what_core_geo_says(db):
    from app.core.geo import is_within_contact_hours, within_geo_fence
    bad = []
    for r in rows(db, "select v.id, v.check_in_time, v.check_in_latitude, v.check_in_longitude, "
                      "v.within_contact_hours, v.geo_verified, v.distance_from_customer_metres, "
                      "c.latitude, c.longitude from collections.visits v "
                      "join collections.cases k on k.id = v.case_id join lending.customers c on c.id = k.customer_id"):
        dist, geo = within_geo_fence(r[2], r[3], r[7], r[8])
        if (is_within_contact_hours(r[1]), geo) != (r[4], r[5]) or abs(dist - float(r[6])) > 0.1:
            bad.append(str(r[0]))
    assert not bad, f"{len(bad)} visits whose flags or distance disagree with core.geo, e.g. {bad[:3]}"


def test_generated_visits_obey_the_product_s_refusals(db):
    """The product refuses an out-of-hours visit and an out-of-fence one that
    is not ADDRESS_ISSUE; a generated book cannot contain either."""
    from app.demo.roster import AGENCY
    assert one(db, "select count(*) from collections.visits where agency_id <> :a and not within_contact_hours",
               a=AGENCY["id"]) == 0
    assert one(db, "select count(*) from collections.visits where agency_id <> :a and not geo_verified "
                   "and outcome <> 'ADDRESS_ISSUE'", a=AGENCY["id"]) == 0


def test_the_awadh_suspension_quotes_the_book(db):
    from app.demo.roster import AWADH
    share = {r[0]: float(r[1]) for r in rows(db, """
        select a.code = 'AGY-AWADH', 100.0 * avg(case when v.geo_verified then 0 else 1 end)
        from collections.visits v join tenancy.agencies a on a.id = v.agency_id
        where v.check_in_time >= '2026-08-01' and v.check_in_time < '2026-09-01' and a.bank_id = :bank
        group by 1""", bank=AWADH.bank_id)}
    reason = one(db, "select suspended_reason from tenancy.agencies where id = :a", a=AWADH.id)
    assert f"{round(share[True])}% of August visits" in reason and f"against {round(share[False])}%" in reason


def test_september_compliance_figure_matches_the_book(db):
    """The README's Compliance & Integrity score for September Girivan
    (fixtures/README.md, "Consent is only asked..."): a hand-computed
    narrative number, not something the product stores, so this is its only
    guard against silent drift (lane L6, 2026-09-30 after seasonality)."""
    from app.demo.roster import BANK
    row = rows(db, """
        select count(*) filter (where within_contact_hours is false) as ooh,
               count(*) filter (where geo_verified is false) as geofence,
               count(*) filter (where customer_met) as met,
               count(*) filter (where customer_met and consent_given is not true) as consent_missing,
               count(*) as total
        from collections.visits v join tenancy.agencies a on a.id = v.agency_id
        where a.bank_id = :bank and v.check_in_time >= '2026-09-01' and v.check_in_time < '2026-10-01'
    """, bank=BANK["id"])[0]
    ooh, geofence, met, consent_missing, total = row
    fraud = one(db, """
        select count(*) from collections.fraud_reviews f
        join collections.visits v on v.id = f.visit_id join tenancy.agencies a on a.id = v.agency_id
        where a.bank_id = :bank and f.verdict = 'CONFIRMED'
          and v.check_in_time >= '2026-09-01' and v.check_in_time < '2026-10-01'
    """, bank=BANK["id"])
    score = round(100 - (ooh + geofence + fraud + consent_missing) / total * 100, 1)
    assert (total, met, consent_missing, ooh, geofence, fraud, score) == (4214, 2284, 705, 106, 166, 0, 76.8)


def test_the_injected_breaches_are_where_the_manifest_says(db):
    truth = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for key, t in truth["agencies"].items():
        gaming = t["injected"].get("fence_gaming", [])
        if gaming:
            got = rows(db, "select outcome, geo_verified from collections.visits where id = any(cast(:ids as uuid[]))",
                       ids=gaming)
            assert len(got) == len(gaming) and all(o == "ADDRESS_ISSUE" and not g for o, g in got), key
        ooh = t["injected"].get("out_of_hours_attempt", [])
        if ooh:
            got = rows(db, "select action, success, created_at from audit.audit_logs where id = any(cast(:ids as uuid[]))",
                       ids=ooh)
            from app.core.geo import is_within_contact_hours
            assert len(got) == len(ooh)
            assert all(a == "CONTACT_HOUR_VIOLATION_ATTEMPT" and not s and not is_within_contact_hours(at)
                       for a, s, at in got), key


def test_the_latent_truth_is_not_in_any_product_table(db):
    """C.4: latent quality lives in the manifest, never in a product column."""
    cols = {r[0] for r in rows(db, "select column_name from information_schema.columns "
                                   "where table_schema not in ('pg_catalog', 'information_schema')")}
    assert not cols & {"skill", "agent_skill", "latent_skill", "skill_mean", "willingness", "true_pay_logit"}


def test_the_analytics_views_are_not_empty(db):
    for view in ("mv_collections_daily", "mv_field_activity_daily"):
        assert one(db, f"select count(*) from analytics.{view}") > 0, view


def test_the_revision_is_one_alembic_knows(db):
    from alembic.script import ScriptDirectory

    from tests.pg.conftest import alembic_cfg
    rev = one(db, "select version_num from public.alembic_version")
    assert ScriptDirectory.from_config(alembic_cfg()).get_revision(rev) is not None


def test_no_open_invite_is_redeemable_with_a_guessable_token(db):
    """Audit HIGH: an open invite in a committed fixture is a public credential
    if its token can be computed. None may hash a string derivable from the
    roster (the pre-fix scheme was sha256("never-issued:<agency key>"))."""
    import hashlib
    from app.demo import roster as R
    guessable = {hashlib.sha256(f"{p}{a.key}".encode()).hexdigest()
                 for a in R.AGENCIES for p in ("never-issued:", "", "invite:")}
    open_hashes = {r[0] for r in rows(db, "select token_sha256 from tenancy.user_invites "
                                          "where accepted_at is null and revoked_at is null")}
    assert open_hashes and not open_hashes & guessable


SECRETISH = r"(secret|token|api_?key|password|otp|jti|totp|hash)"
#: The only secret-like columns a committed demo book may fill, and why each is safe.
#: Everything else matching SECRETISH must be empty: a session, reset token, device
#: secret, TOTP seed or push token in a public fixture is a credential.
ALLOWED_FILLED = {
    "tenancy.users.hashed_password",         # all the unusable marker (test above)
    "tenancy.users.must_change_password",    # booleans, checked false below
    "tenancy.users.totp_enabled",
    "tenancy.user_invites.token_sha256",     # random preimage (test above)
}


def test_no_other_secret_like_value_is_in_the_fixture(db):
    """Coordinator, after the invite finding: scan every secret-like column."""
    cols = rows(db, "select table_schema, table_name, column_name from information_schema.columns "
                    "where table_schema not in ('pg_catalog', 'information_schema') "
                    "and column_name ~* :rx", rx=SECRETISH)
    filled = {f"{s}.{t}.{c}" for s, t, c in cols
              if one(db, f'select count(*) from "{s}"."{t}" where "{c}" is not null') > 0}
    assert filled <= ALLOWED_FILLED, f"secret-like columns with values: {sorted(filled - ALLOWED_FILLED)}"
    assert one(db, "select count(*) from tenancy.users where must_change_password or totp_enabled") == 0


# ── loan_dpd_history coverage (P3: the bank Overview reads it) ──────────────
def test_no_loan_is_disbursed_after_the_anchor(db):
    from app.demo.roster import ANCHOR_DATE
    assert one(db, "select count(*) from lending.loans where disbursement_date > :d", d=ANCHOR_DATE) == 0


def test_every_loan_has_a_reading_at_the_anchor_and_aravalli_says_it_is_a_backfill(db):
    from app.demo.roster import AGENCY, ANCHOR_DATE
    assert one(db, "select count(*) from lending.loans l where not exists (select 1 from lending.loan_dpd_history h "
                   "where h.loan_id = l.id and h.as_of_date = :d)", d=ANCHOR_DATE) == 0
    # Aravalli's (B15's) loans: one row, the anchor, flagged as not observed point-in-time.
    # B15's book is the loans with no LEDGER reading: exactly the 1,478 transformed ones.
    got = rows(db, "select h.source, h.is_backfill, h.observed_pit, count(*), count(distinct h.as_of_date) "
                   "from lending.loan_dpd_history h where not exists (select 1 from lending.loan_dpd_history g "
                   "where g.loan_id = h.loan_id and g.source = 'LEDGER') group by 1, 2, 3")
    assert got == [("TRANSFORM_CURRENT", True, False, 1478, 1)]
    assert one(db, "select count(*) from lending.loan_dpd_history where source = 'TRANSFORM_CURRENT'") == 1478
    assert one(db, "select count(*) from lending.loan_dpd_history where source = 'TRANSFORM_CURRENT' "
                   "and agency_id is not null and agency_id <> :a", a=AGENCY["id"]) == 0


def test_month_end_rows_are_calendar_month_ends_only(db):
    assert one(db, "select count(*) from lending.loan_dpd_history where is_month_end "
                   "and as_of_date <> (date_trunc('month', as_of_date) + interval '1 month - 1 day')::date") == 0
    assert one(db, "select count(distinct as_of_date) from lending.loan_dpd_history "
                   "where as_of_date between '2026-09-01' and '2026-09-22' and source = 'LEDGER'") == 22


def test_instalments_are_inside_the_stated_window(db):
    from app.demo.books import INSTALMENT_WINDOW
    lo, hi = INSTALMENT_WINDOW
    assert one(db, "select count(*) from lending.loan_instalments where due_date < :lo or due_date > :hi",
               lo=lo, hi=hi) == 0
    readme = (BACKEND / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert f"{lo.isoformat()}" in readme and f"{hi.isoformat()}" in readme


# ── the performing book and borrower detail (lane L6, 2026-09-30) ───────────
def _performing_prefix(bank_key: str) -> str:
    from app.demo import roster as R
    from app.demo.performing import BOOK_NUMBER
    return f"{R.BANKS[bank_key]['code'][:3]}{BOOK_NUMBER[bank_key]:02d}"


def test_the_performing_book_is_the_manifest_s_and_never_delinquent(db):
    truth = json.loads(MANIFEST.read_text(encoding="utf-8"))["performing"]
    assert truth, "the manifest has no performing book"
    for bank_key, t in truth.items():
        p = _performing_prefix(bank_key) + "%"
        loans = "from lending.loans l join lending.customers c on c.id = l.customer_id where c.customer_ref like :p"
        assert one(db, f"select count(*) {loans}", p=p) == t["loans"]
        assert one(db, f"select count(*) {loans} and (l.dpd <> 0 or l.status <> 'ACTIVE' or l.overdue_amount <> 0)",
                   p=p) == 0
        assert one(db, f"select count(*) {loans} and exists (select 1 from collections.placements x "
                       "where x.loan_id = l.id)", p=p) == 0
        hist = ("from lending.loan_dpd_history h join lending.loans l on l.id = h.loan_id "
                "join lending.customers c on c.id = l.customer_id where c.customer_ref like :p")
        assert one(db, f"select count(*) {hist}", p=p) == t["history_rows"]
        assert one(db, f"select count(*) {hist} and (h.dpd <> 0 or h.agency_id is not null)", p=p) == 0


def test_most_of_girivan_s_live_book_is_current(db):
    """Why the performing book exists: before it, 27-36% of the live book was
    CURRENT, a lender entirely in collections."""
    from app.demo.roster import ANCHOR_DATE, BANK
    live, current = rows(db, "select count(*), count(*) filter (where dpd = 0) from lending.loan_dpd_history "
                             "where bank_id = :b and as_of_date = :d and loan_status in ('ACTIVE', 'NPA')",
                         b=BANK["id"], d=ANCHOR_DATE)[0]
    assert current / live >= 0.65, (current, live)


def test_borrower_emails_are_on_reserved_test_domains_only(db):
    assert one(db, "select count(*) from lending.customers where email is not null "
                   "and split_part(email, '@', 2) not like '%.test'") == 0
    assert one(db, "select count(*) from lending.customers where email is not null") > 0


def test_the_restored_fixture_is_at_the_code_s_head_revision(db):
    """The fixture must test today's code against today's schema.

    The dump is a snapshot at whatever revision it was built on, so the
    restore alone leaves the suite a few migrations behind — invisibly,
    because nothing fails, it just tests the wrong thing. On 2026-10-01 that
    was the pre-v2_0017 unindexed v_visit_to_pay: the bank-overview KPI query
    never returned and backend-pg burned six hours on every branch.
    """
    from alembic.script import ScriptDirectory
    from tests.pg.conftest import alembic_cfg

    head = ScriptDirectory.from_config(alembic_cfg()).get_current_head()
    with db.connect() as c:
        stamped = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert stamped == head, (
        f"the restored fixture is at {stamped}, the code is at {head} — "
        "the db fixture must upgrade after restoring, as docker-entrypoint.sh does"
    )
