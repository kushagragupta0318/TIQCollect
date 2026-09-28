# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B16, d4) — NEW. The demo tenancy around the B15 book: every
#   Appendix C row that is master data rather than a book. Runs AFTER
#   scripts/migrate_v1_to_v2.py on the same v2 database (B15 writes Girivan,
#   NCR and Aravalli; this adds everything else) and is deterministic: every
#   id is roster.new_id on a stable key, every draw is seeded.
#
#   What it writes:
#     - Kumaon Finance Ltd, its bank users, regions and branches;
#     - Girivan's other three zones, 18 more cities and their branches;
#     - agencies 2-9 and Almora: identity, contract, 40 contract terms
#       (commission by bucket, authorised product x bucket), coverage;
#     - each agency's Operations Head (AGENCY_ADMIN), managers and agents,
#       with DRA certificates (about 5% expired at the anchor date), women
#       among them, bases in the agency's localities;
#     - Appendix C.3's documents for EVERY agency (Aravalli included, which
#       B15 left without any), with the specimen PDFs' sha256;
#     - the onboarding audit trail, dated (draft, invite, acceptance, uploads,
#       activation; suspension for Awadh; Hooghly still waiting);
#     - Aravalli's 18 agents: gender and DRA certificate, master data B15 left
#       NULL (coordinator Q2, 2026-09-28). Nothing else of Aravalli changes.
#   Every password hash is unusable (core.security.disabled_password_hash).
# ────────────────────────────────────────────────────────────────────────────
"""Write the demo tenancy (banks, geography, agencies, people, documents)."""
from __future__ import annotations

import hashlib
import math
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import numpy as np
import sqlalchemy as sa
from sqlalchemy.engine import Connection

import app.models  # noqa: F401 — registers every table on the metadata
from app.core.database import Base
from app.core.security import disabled_password_hash
from app.demo import documents as D
from app.demo import roster as R
from app.models.audit_log import AuditAction

T = {t.name: t for t in Base.metadata.sorted_tables}

IST = timezone(timedelta(hours=5, minutes=30))


def at(d: date, hh: int = 10, mm: int = 0) -> datetime:
    """An IST wall-clock moment on a date, stored tz-aware."""
    return datetime.combine(d, time(hh, mm), tzinfo=IST)


def insert(conn: Connection, table: str, rows: list[dict]) -> int:
    """Bulk insert. An executemany needs one column set per batch, so rows are
    grouped by the keys they carry; a column a row leaves out keeps its
    server default rather than becoming NULL."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r)), []).append(r)
    for batch in groups.values():
        for i in range(0, len(batch), 2000):
            conn.execute(T[table].insert(), batch[i:i + 2000])
    return len(rows)


# ── what the book generator needs back ──────────────────────────────────────
@dataclass
class AgentRec:
    idx: int                      # the ledger's agent index (AG{idx:03d})
    id: str
    user_id: str
    name: str
    gender: str
    city: str
    lat: float
    lon: float
    manager_user_id: str
    joined_on: date


@dataclass
class AgencyWorld:
    roster: R.AgencyRoster
    number: int                   # 2..10, stable: order in R.AGENCIES
    admin_user_id: str | None
    manager_ids: list[str]
    agents: list[AgentRec] = field(default_factory=list)
    branches_by_city: dict = field(default_factory=dict)
    region_by_city: dict = field(default_factory=dict)


@dataclass
class World:
    agencies: dict                # key -> AgencyWorld (generated agencies only)
    bank_admin: dict              # bank key -> user id
    counts: dict


def region_key(bank_key: str, code: str) -> str:
    """B15 keyed Girivan's regions by code alone; Kumaon's are prefixed so
    the two banks' "WEST" / "MH" never share an id."""
    return code if bank_key == "GIRIVAN" else f"{bank_key}:{code}"


def _region_rows(bank_key: str, bank_id: str, rows_in: list, known: list) -> list[dict]:
    paths = {}
    for level, code, _n, parent, _a, _b in known + rows_in:
        paths[code] = code if parent is None else f"{paths[parent]}.{code}"
    return [dict(id=R.new_id("region", region_key(bank_key, code)), bank_id=bank_id,
                 parent_id=(R.new_id("region", region_key(bank_key, parent)) if parent else None),
                 level=level, code=code, name=name, path=paths[code], latitude=lat, longitude=lon, is_active=True)
            for level, code, name, parent, lat, lon in rows_in]


def _dra_no(rng: np.random.Generator, issued: date) -> str:
    return f"IIBF/DRA/{issued.year}/{int(rng.integers(100000, 999999))}"


def _people_pool(culture: str) -> dict:
    return R.NAME_POOLS[culture]


def draw_person(rng: np.random.Generator, culture: str, female: bool, taken: set) -> str:
    pool = _people_pool(culture)
    firsts = pool["female" if female else "male"]
    for _ in range(200):
        name = f"{firsts[int(rng.integers(len(firsts)))]} {pool['surnames'][int(rng.integers(len(pool['surnames'])))]}"
        if name not in taken:
            taken.add(name)
            return name
    raise R.RosterError(f"name pool {culture} exhausted")


def jitter(rng: np.random.Generator, lat: float, lon: float, metres: float) -> tuple[float, float]:
    """A point within ~`metres` of (lat, lon), uniform in the disc."""
    r = metres * math.sqrt(float(rng.random()))
    th = 2 * math.pi * float(rng.random())
    return (round(lat + (r * math.cos(th)) / 111_320, 6),
            round(lon + (r * math.sin(th)) / (111_320 * math.cos(math.radians(lat))), 6))


def _audit(conn_rows: list, *, when: datetime, action: AuditAction, user_id, bank_id, agency_id,
           entity_type: str, entity_id: str, details: dict, success: bool = True, key: str) -> None:
    conn_rows.append(dict(id=R.new_id("audit", key), created_at=when, user_id=user_id, bank_id=bank_id,
                          agency_id=agency_id, action=action.value, entity_type=entity_type,
                          entity_id=str(entity_id)[:50], details=details, success=success))


def unredeemable_token_hash() -> str:
    """The token_sha256 of an invite nobody can accept: the hash of random bytes
    that are discarded at once. (Audit HIGH, 2026-09-28: it was the sha256 of a
    fixed string in this source, so anyone could redeem Hooghly's open invite.)"""
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


def _user_id_by_email(conn: Connection, email: str) -> str:
    uid = conn.execute(sa.select(T["users"].c.id).where(T["users"].c.email == email)).scalar_one_or_none()
    if uid is None:
        raise R.RosterError(f"expected user {email} is not in the database (run the B15 transform first)")
    return str(uid)


# ── the world ───────────────────────────────────────────────────────────────
def build_world(conn: Connection, *, agency_keys: tuple[str, ...], agents_cap: int | None = None,
                agents_scale: float = 1.0, seed: int = 20260922) -> World:
    """Write the tenancy for `agency_keys` (and, always, the documents and
    onboarding trail for Aravalli, which B15 wrote without them)."""
    rng = np.random.default_rng(seed)
    counts: dict[str, int] = {}
    unusable = disabled_password_hash
    audit_rows: list[dict] = []
    girivan_admin = R.new_id("user", "ananya.iyer")
    kumaon_admin = R.new_id("user", f"KUMAON:{R.KUMAON_BANK_USERS[0][0]}")
    bank_admin = {"GIRIVAN": girivan_admin, "KUMAON": kumaon_admin}
    wanted = [a for a in R.AGENCIES if a.key in agency_keys]
    need_kumaon = any(a.bank_key == "KUMAON" for a in wanted)

    # 1. Kumaon Finance (only when a Kumaon agency is generated)
    if need_kumaon:
        counts["banks"] = insert(conn, "banks", [R.KUMAON_BANK])
        counts["users(kumaon bank)"] = insert(conn, "users", [
            dict(id=R.new_id("user", f"KUMAON:{local}"), email=f"{local}@{R.KUMAON_DOMAIN}", phone=phone,
                 full_name=name, role=role, hashed_password=unusable(), bank_id=R.KUMAON_BANK["id"],
                 agency_id=None, is_active=True, is_verified=True, must_change_password=False,
                 totp_enabled=False, failed_login_attempts=0)
            for local, name, role, phone in R.KUMAON_BANK_USERS])

    # 2. geography
    region_rows = _region_rows("GIRIVAN", R.BANK["id"], R.GIRIVAN_REGIONS_EXTRA, list(R.REGIONS))
    if need_kumaon:
        region_rows += _region_rows("KUMAON", R.KUMAON_BANK["id"], R.KUMAON_REGIONS, [])
    counts["regions"] = insert(conn, "regions", region_rows)
    branch_rows = []
    for bank_key, bank, extra in (("GIRIVAN", R.BANK, R.GIRIVAN_BRANCHES_EXTRA),
                                  ("KUMAON", R.KUMAON_BANK, R.KUMAON_BRANCHES)):
        if bank_key == "KUMAON" and not need_kumaon:
            continue
        for code, (city, name) in extra.items():
            loc = R.LOCALITIES[city][0]
            branch_rows.append(dict(id=R.new_id("branch", region_key(bank_key, code)), bank_id=bank["id"],
                                    region_id=R.new_id("region", region_key(bank_key, city)), branch_code=code,
                                    name=name, address=f"{loc[0]}, {dict((c, n) for (lv, c, n, *_x) in R.REGIONS + R.GIRIVAN_REGIONS_EXTRA if lv == 'CITY').get(city, city.title())} {loc[3]}",
                                    latitude=loc[1], longitude=loc[2], is_active=True))
    counts["branches"] = insert(conn, "branches", branch_rows)
    # Every branch (B15's NCR ones included) by city, for the book's loans.
    city_of_region = {rid: code for code, rid in
                      ((c, R.new_id("region", region_key(bk, c)))
                       for bk, rows in (("GIRIVAN", R.REGIONS + R.GIRIVAN_REGIONS_EXTRA), ("KUMAON", R.KUMAON_REGIONS))
                       for (lv, c, *_x) in rows if lv == "CITY")}
    branches_by_bank_city: dict[tuple, list] = {}
    for b in conn.execute(sa.select(T["branches"].c.bank_id, T["branches"].c.branch_code,
                                    T["branches"].c.region_id)).all():
        city = city_of_region.get(str(b.region_id))
        if city:
            branches_by_bank_city.setdefault((str(b.bank_id), city), []).append(b.branch_code)

    # 3. agencies, contracts, terms, coverage
    worlds: dict[str, AgencyWorld] = {}
    new_agencies = [a for a in wanted if a.key != "ARAVALLI"]
    for a in new_agencies:
        row = dict(a.row, created_by=bank_admin[a.bank_key])
        conn.execute(T["agencies"].insert(), [row])
    counts["agencies"] = len(new_agencies)
    contracts, terms, cover = [], [], []
    for a in new_agencies:
        contracts.append(dict(a.contract, approved_at=(at(a.onboarded, 11) if a.onboarded else None)))
        for lt in R.LOAN_TYPES:
            for b in R.DPD_BUCKETS:
                terms.append(dict(id=R.new_id("term", f"{a.key}:{lt}:{b}"), bank_id=a.bank_id, agency_id=a.id,
                                  contract_id=a.contract["id"], loan_type=lt, dpd_bucket=b,
                                  commission_pct=Decimal(a.commission[b]),
                                  is_authorised=(lt in a.products and b in a.buckets)))
        cover.append(dict(id=R.new_id("agency_region", f"{a.key}:{a.agency_region}"), bank_id=a.bank_id,
                          agency_id=a.id, contract_id=a.contract["id"],
                          region_id=R.new_id("region", region_key(a.bank_key, a.agency_region))))
    counts["agency_contracts"] = insert(conn, "agency_contracts", contracts)
    counts["agency_contract_terms"] = insert(conn, "agency_contract_terms", terms)
    counts["agency_regions"] = insert(conn, "agency_regions", cover)

    # 4. people. Phones: 9 + two-digit agency number + 7 digits, unique by construction.
    existing_phones = {p for (p,) in conn.execute(sa.select(T["users"].c.phone)).all()}
    existing_emails = {e for (e,) in conn.execute(sa.select(T["users"].c.email)).all()}
    users, agents = [], []
    for a in new_agencies:
        n = R.AGENCIES.index(a) + 1
        city_culture = R.CITY_CULTURE[a.serves[0]]
        w = AgencyWorld(roster=a, number=n, admin_user_id=None, manager_ids=[])
        for city in a.serves:
            w.branches_by_city[city] = sorted(branches_by_bank_city.get((a.bank_id, city), []))
            w.region_by_city[city] = R.new_id("region", region_key(a.bank_key, city))
        taken = {p["name"] for p in a.people} | set(a.managers)

        def user(key: str, name: str, role: str, phone: str, joined: date, email: str | None = None) -> str:
            email = email or R.email_for(name, a.domain)
            if email in existing_emails or phone in existing_phones:
                raise R.RosterError(f"duplicate login {email} / {phone}")
            existing_emails.add(email)
            existing_phones.add(phone)
            uid = R.new_id("user", email)
            users.append(dict(id=uid, email=email, phone=phone, full_name=name, role=role,
                              hashed_password=unusable(), bank_id=a.bank_id, agency_id=a.id, is_active=True,
                              is_verified=True, must_change_password=False, totp_enabled=False,
                              failed_login_attempts=0, created_at=at(joined, 9, 30)))
            return uid

        if a.onboarded is None:                      # Hooghly: the invite is still open
            worlds[a.key] = w
            continue
        ops = a.ops_head
        w.admin_user_id = user("admin", ops["name"], "AGENCY_ADMIN", ops["phone"][3:], a.onboarded, ops["email"])
        for mi, mname in enumerate(a.managers):
            w.manager_ids.append(user(f"mgr{mi}", mname, "AGENCY_MANAGER", f"9{n + 10:02d}9{mi:06d}",
                                      a.onboarded + timedelta(days=3)))
        n_agents = int(round(a.n_agents * agents_scale))
        n_agents = n_agents if agents_cap is None else min(n_agents, agents_cap)
        weights = np.array([2.0] + [1.0] * (len(a.serves) - 1))
        for i in range(n_agents):
            female = bool(rng.random() < 0.35)
            city = a.serves[int(rng.choice(len(a.serves), p=weights / weights.sum()))]
            culture = R.CITY_CULTURE[city] if rng.random() < 0.8 else city_culture
            name = draw_person(rng, culture, female, taken)
            joined = a.onboarded + timedelta(days=int(rng.integers(0, 45)) if i < n_agents * 0.8
                                             else int(rng.integers(45, max(46, (R.ANCHOR_DATE - a.onboarded).days - 20))))
            uid = user(f"agent{i}", name, "FIELD_AGENT", f"9{n + 10:02d}{i:07d}", joined)
            loc = R.LOCALITIES[city][int(rng.integers(len(R.LOCALITIES[city])))]
            lat, lon = jitter(rng, loc[1], loc[2], 1500)
            issued = joined - timedelta(days=int(rng.integers(90, 900)))
            expires = R.plus_years(issued, 3)
            if rng.random() < 0.05:                  # a few lapsed, for the compliance tile
                expires = R.ANCHOR_DATE - timedelta(days=int(rng.integers(5, 120)))
            aid = R.new_id("agent", f"{a.key}:{i}")
            mgr = w.manager_ids[i % len(w.manager_ids)]
            suspended = a.row["status"] == "SUSPENDED"
            agents.append(dict(
                id=aid, bank_id=a.bank_id, agency_id=a.id, user_id=uid,
                employee_code=f"{a.row['code'][4:7]}{i + 1:04d}", id_card_number=f"{a.row['code'][4:]}-{i + 1:04d}",
                gender="FEMALE" if female else "MALE", base_latitude=lat, base_longitude=lon,
                territory=f"{loc[0]}, {city.replace('_', ' ').title()}",
                territory_region_id=w.region_by_city[city],
                languages_spoken=sorted({culture, "HINDI", "ENGLISH"} if culture != "HINDI" else {"HINDI", "ENGLISH"}),
                specialization=("BOTH" if rng.random() < 0.6 else ("SECURED" if rng.random() < 0.5 else "UNSECURED")),
                max_cases_per_day=int(rng.integers(12, 16)),
                vehicle_type=("TWO_WHEELER" if rng.random() < 0.8 else "FOUR_WHEELER"),
                status=("SUSPENDED" if suspended else "ON_DUTY"), tier="TIER_2", ranking_score=0.0,
                joined_on=joined, dra_certificate_no=_dra_no(rng, issued), dra_certificate_expires_on=expires,
                manager_user_id=mgr,
                suspended_at=(a.row["suspended_at"] if suspended else None),
                suspended_reason=(f"Agency suspended by the bank: {a.row['suspended_reason']}" if suspended else None)))
            w.agents.append(AgentRec(idx=i, id=aid, user_id=uid, name=name, gender=agents[-1]["gender"], city=city,
                                     lat=lat, lon=lon, manager_user_id=mgr, joined_on=joined))
        worlds[a.key] = w
    counts["users"] = insert(conn, "users", users)
    counts["agents"] = insert(conn, "agents", agents)

    # 5. Aravalli: gender and DRA certificate on the 18 v1 agents (master data
    #    B15 left NULL; coordinator Q2), and three women who joined in
    #    September 2026 with no history (coordinator, option (b)). Gender is
    #    the roster's explicit data, looked up by the v1 name; an agent the
    #    roster does not list aborts rather than being guessed.
    if "ARAVALLI" in agency_keys:
        ag, us = T["agents"], T["users"]
        rows = conn.execute(sa.select(ag.c.id, ag.c.joined_on, ag.c.created_at, us.c.full_name)
                            .join(us, us.c.id == ag.c.user_id)
                            .where(ag.c.agency_id == R.AGENCY["id"]).order_by(ag.c.employee_code)).all()
        unknown = sorted(r.full_name for r in rows if r.full_name not in R.ARAVALLI_V1_AGENT_GENDER)
        if unknown or len(rows) != len(R.ARAVALLI_V1_AGENT_GENDER):
            raise R.RosterError(f"Aravalli agents the roster does not list: {unknown} ({len(rows)} rows)")
        for r in rows:
            joined = r.joined_on or r.created_at.date()
            issued = joined - timedelta(days=int(rng.integers(120, 900)))
            conn.execute(ag.update().where(ag.c.id == r.id).values(
                gender=R.ARAVALLI_V1_AGENT_GENDER[r.full_name], dra_certificate_no=_dra_no(rng, issued),
                dra_certificate_expires_on=R.plus_years(issued, 3)))
        counts["aravalli v1 agents: gender + DRA set"] = len(rows)
        # B15 kept v1's own user ids, so its staff are found by their roster e-mail.
        mgr_by_name = {name: _user_id_by_email(conn, f"{local}@{R.AGENCY_DOMAIN}")
                       for local, name, role in R.V1_STAFF.values()}
        new_users, new_agents = [], []
        for name, gender, joined, city, loc_i, manager, code, card, phone in R.ARAVALLI_NEW_AGENTS:
            email = R.email_for(name, R.AGENCY_DOMAIN)
            if email in existing_emails or phone in existing_phones:
                raise R.RosterError(f"duplicate login {email} / {phone}")
            uid = R.new_id("user", email)
            new_users.append(dict(id=uid, email=email, phone=phone, full_name=name, role="FIELD_AGENT",
                                  hashed_password=unusable(), bank_id=R.BANK["id"], agency_id=R.AGENCY["id"],
                                  is_active=True, is_verified=True, must_change_password=False, totp_enabled=False,
                                  failed_login_attempts=0, created_at=at(joined, 9, 30)))
            loc = R.LOCALITIES[city][loc_i]
            lat, lon = jitter(rng, loc[1], loc[2], 800)
            issued = joined - timedelta(days=int(rng.integers(60, 400)))
            new_agents.append(dict(
                id=R.new_id("agent", f"ARAVALLI:{code}"), bank_id=R.BANK["id"], agency_id=R.AGENCY["id"],
                user_id=uid, employee_code=code, id_card_number=card, gender=gender, base_latitude=lat,
                base_longitude=lon, territory=f"{loc[0]}, {city.title()}",
                territory_region_id=R.new_id("region", region_key("GIRIVAN", city)),
                languages_spoken=["ENGLISH", "HINDI"],
                specialization="BOTH", max_cases_per_day=12, vehicle_type="TWO_WHEELER", status="ON_DUTY",
                tier="TIER_3", ranking_score=0.0, joined_on=joined, dra_certificate_no=_dra_no(rng, issued),
                dra_certificate_expires_on=R.plus_years(issued, 3),
                manager_user_id=mgr_by_name[manager]))
        counts["aravalli new agents"] = insert(conn, "users", new_users) and insert(conn, "agents", new_agents)

    # 6. documents and the onboarding trail, for every agency in scope
    docs = []
    for a in [x for x in R.AGENCIES if x.key in agency_keys]:
        admin = bank_admin[a.bank_key]
        agency_admin = (worlds[a.key].admin_user_id if a.key in worlds       # Aravalli: B15's AGENCY_ADMIN
                        else _user_id_by_email(conn, f"meera.khanna@{R.AGENCY_DOMAIN}"))
        start = a.onboarded or a.invite_sent
        draft = start - timedelta(days=28)
        base = dict(bank_id=a.bank_id, agency_id=a.id)
        _audit(audit_rows, when=at(draft, 11, 5), action=AuditAction.AGENCY_ONBOARDED, user_id=admin,
               entity_type="agency", entity_id=a.id, key=f"{a.key}:draft", **base,
               details={"step": "draft created", "legal_name": a.row["legal_name"],
                        "contract_no": a.contract["contract_no"]})
        _audit(audit_rows, when=at(draft + timedelta(days=1), 12, 40), action=AuditAction.USER_INVITED,
               user_id=admin, entity_type="agency", entity_id=a.id, key=f"{a.key}:invite", **base,
               details={"invitee": a.ops_head["email"], "role": "AGENCY_ADMIN", "channel": "LINK"})
        uploader = admin
        if a.onboarded is not None:
            _audit(audit_rows, when=at(draft + timedelta(days=3), 10, 15), action=AuditAction.INVITE_ACCEPTED,
                   user_id=agency_admin, entity_type="user", entity_id=agency_admin, key=f"{a.key}:accept",
                   **base, details={"role": "AGENCY_ADMIN"})
            uploader = agency_admin
        for k, s in enumerate(D.specimens(a)):
            doc_id = R.new_id("agency_document", f"{a.key}:{s.doc_type}")
            uploaded = draft + timedelta(days=4 + k)
            verified = a.onboarded is not None
            docs.append(dict(id=doc_id, **base, doc_type=s.doc_type, storage_key=s.storage_key,
                             file_name=f"{s.doc_type.lower()}.pdf", content_type="application/pdf",
                             size_bytes=len(s.body), sha256=s.sha256, scan_status="CLEAN",
                             status=("VERIFIED" if verified else "UPLOADED"), issued_on=s.issued_on,
                             expires_on=s.expires_on, uploaded_by=uploader,
                             verified_by=(admin if verified else None),
                             verified_at=(at(uploaded + timedelta(days=2), 15, 20) if verified else None),
                             created_at=at(uploaded, 14, 10)))
            _audit(audit_rows, when=at(uploaded, 14, 10), action=AuditAction.DOCUMENT_UPLOADED, user_id=uploader,
                   entity_type="agency_document", entity_id=doc_id, key=f"{a.key}:doc:{s.doc_type}", **base,
                   details={"doc_type": s.doc_type, "sha256": s.sha256, "file_name": f"{s.doc_type.lower()}.pdf"})
        if a.onboarded is not None:
            _audit(audit_rows, when=at(a.onboarded, 10, 0), action=AuditAction.AGENCY_ACTIVATED, user_id=admin,
                   entity_type="agency", entity_id=a.id, key=f"{a.key}:activate", **base,
                   details={"contract_no": a.contract["contract_no"], "documents_verified": len(D.specimens(a))})
        if a.row["status"] == "SUSPENDED":
            _audit(audit_rows, when=a.row["suspended_at"], action=AuditAction.AGENCY_SUSPENDED, user_id=admin,
                   entity_type="agency", entity_id=a.id, key=f"{a.key}:suspend", **base,
                   details={"reason": a.row["suspended_reason"]})
        if a.invite_sent is not None:                # the open invite (Hooghly)
            token = unredeemable_token_hash()
            conn.execute(T["user_invites"].insert(), [dict(
                id=R.new_id("invite", a.key), bank_id=a.bank_id, agency_id=a.id, purpose="AGENCY_MASTER_LOGIN",
                email=a.ops_head["email"], phone=a.ops_head["phone"][3:], full_name=a.ops_head["name"],
                role="AGENCY_ADMIN", token_sha256=token, delivery_channel="LINK", invited_by=admin,
                expires_at=at(a.invite_sent + timedelta(days=14), 23, 59), created_at=at(a.invite_sent, 16, 45))])
            counts["user_invites"] = counts.get("user_invites", 0) + 1
    counts["agency_documents"] = insert(conn, "agency_documents", docs)
    # Each contract's signed agreement is one of the documents just written.
    for a in [x for x in R.AGENCIES if x.key in agency_keys]:
        conn.execute(T["agency_contracts"].update().where(T["agency_contracts"].c.id == a.contract["id"])
                     .values(agreement_document_id=R.new_id("agency_document", f"{a.key}:AGREEMENT")))
    counts["audit_logs(onboarding)"] = insert(conn, "audit_logs", audit_rows)
    return World(agencies=worlds, bank_admin=bank_admin, counts=counts)
