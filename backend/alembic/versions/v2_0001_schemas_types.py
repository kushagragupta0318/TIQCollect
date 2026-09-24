"""v2 baseline, step 1 of 8: the ten domain schemas, btree_gist, and the 28
native enum types (docs/DATA-MODEL-V2.md §9.1, task B11).

The v2 chain starts from nothing. The 18 v1 revisions live in
alembic/versions_v1/ as history, off the upgrade path: v1 and v2 share no
upgrade path because v2 is a new database (§9.2's transform moves the data).

Enum values are FROZEN here as literals, read from the models on 2026-09-24.
A later value is its own `ALTER TYPE … ADD VALUE` revision; editing this list
would change what an already-migrated database is said to contain.

Revision ID: v2_0001
Revises: (none)
Create Date: 2026-09-24
"""
from alembic import op

revision = "v2_0001"
down_revision = None
branch_labels = ("v2",)
depends_on = None

SCHEMAS = ("tenancy", "lending", "collections", "workforce", "planning", "ml", "ai", "strategy", "audit",
           "analytics")

# (type name, values) — all in `public`, shared across schemas (§2.1).
ENUMS = (
    ('agent_spec_enum', ('SECURED', 'UNSECURED', 'BOTH',)),
    ('agent_status_enum', ('ON_DUTY', 'OFF_DUTY', 'ON_LEAVE', 'SUSPENDED',)),
    ('agent_tier_enum', ('TIER_1', 'TIER_2', 'TIER_3',)),
    ('audit_action_enum', ('LOGIN', 'LOGOUT', 'LOGIN_FAILED', 'TOKEN_REFRESH', 'CASE_ASSIGNED', 'CASE_UPDATED', 'VISIT_RECORDED', 'PAYMENT_SUBMITTED', 'PAYMENT_VERIFIED', 'PTP_SET', 'PTP_UPDATED', 'DOCUMENT_UPLOADED', 'SOS_TRIGGERED', 'SOS_RESOLVED', 'BEAT_GENERATED', 'BEAT_MODIFIED', 'AGENT_STATUS_CHANGED', 'CONTACT_HOUR_VIOLATION_ATTEMPT', 'ROLE_VIOLATION_ATTEMPT', 'DEVICE_MISMATCH', 'DATA_EXPORT', 'ANOMALY_REVIEWED', 'MODEL_CANDIDATE_APPROVED', 'MODEL_CANDIDATE_REJECTED', 'MODEL_PROMOTED', 'VOICE_CALL_PLACED', 'VOICE_CALL_REFUSED', 'DEVICE_RESET',)),
    ('beat_status_enum', ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED',)),
    ('borrower_disposition_enum', ('WILL_PAY', 'MAY_PAY', 'NO_COMMITMENT', 'HARDSHIP', 'DISPUTE', 'REFUSES',)),
    ('call_outcome_enum', ('ANSWERED', 'NO_ANSWER', 'BUSY', 'DECLINED', 'SWITCHED_OFF', 'WRONG_NUMBER',)),
    ('case_priority_enum', ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL',)),
    ('case_status_enum', ('UNASSIGNED', 'ASSIGNED', 'IN_PROGRESS', 'PTP_SET', 'PARTIALLY_PAID', 'PAID', 'ESCALATED', 'CLOSED', 'WRITTEN_OFF',)),
    ('default_reason_enum', ('JOB_LOSS', 'SALARY_CUT', 'BUSINESS_FAILURE', 'MEDICAL', 'DEATH_IN_FAMILY', 'MARITAL_DISPUTE', 'ALREADY_PAID', 'AMOUNT_DISPUTED', 'FRAUD_CLAIM', 'OVER_LEVERAGED', 'OTHER',)),
    ('dpd_bucket_enum', ('CURRENT', 'BUCKET_1', 'BUCKET_2', 'BUCKET_3', 'NPA',)),
    ('escalation_reason_enum', ('CUSTOMER_HOSTILE', 'CUSTOMER_ABSCONDED', 'DISPUTED_AMOUNT', 'LEGAL_NOTICE_REQUIRED', 'PROPERTY_DISPUTE', 'OTHER',)),
    ('leave_status_enum', ('REQUESTED', 'APPROVED', 'REJECTED', 'CANCELLED',)),
    ('leave_type_enum', ('SICK_LEAVE', 'CASUAL_LEAVE', 'EARNED_LEAVE', 'ABSENT',)),
    ('loan_status_enum', ('ACTIVE', 'CLOSED', 'WRITTEN_OFF', 'SETTLED', 'NPA',)),
    ('loan_type_enum', ('HOME', 'AUTO', 'PERSONAL', 'BUSINESS', 'GOLD', 'CREDIT_CARD', 'EDUCATION', 'MICROFINANCE',)),
    ('location_source_enum', ('HEARTBEAT', 'CHECK_IN', 'VISIT', 'SOS',)),
    ('model_candidate_state_enum', ('TRAINING', 'VALIDATING', 'COMPARING', 'PENDING_APPROVAL', 'APPROVED', 'PROMOTED', 'INSUFFICIENT_DATA', 'REJECTED_VALIDATION', 'REJECTED_COMPARISON', 'REJECTED_BY_HUMAN', 'FAILED',)),
    ('not_met_reason_enum', ('PREMISES_LOCKED', 'CUSTOMER_AWAY', 'WRONG_ADDRESS', 'CUSTOMER_ABSCONDED', 'NEIGHBOR_MET', 'OTHER',)),
    ('payment_mode_enum', ('CASH', 'UPI', 'NEFT', 'RTGS', 'CHEQUE', 'DD', 'ONLINE', 'BANK_DIRECT',)),
    ('payment_status_enum', ('PENDING_VERIFICATION', 'VERIFIED', 'REJECTED', 'REVERSED',)),
    ('person_met_enum', ('BORROWER', 'CO_BORROWER', 'SPOUSE', 'PARENT', 'SIBLING', 'CHILD', 'RELATIVE', 'EMPLOYER', 'NEIGHBOR', 'SECURITY', 'OTHER',)),
    ('ptp_status_enum', ('ACTIVE', 'HONORED', 'BROKEN', 'PARTIALLY_HONORED', 'EXPIRED', 'RESCHEDULED',)),
    ('recovery_potential_enum', ('HIGH', 'MEDIUM', 'LOW',)),
    ('review_verdict_enum', ('CONFIRMED', 'DISMISSED',)),
    ('risk_category_enum', ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL',)),
    ('user_role_enum', ('FIELD_AGENT', 'AGENCY_MANAGER', 'AGENCY_ADMIN', 'PLATFORM_ADMIN', 'BANK_ADMIN', 'BANK_ANALYST', 'BANK_TECHOPS', 'SERVICE',)),
    ('visit_outcome_enum', ('PAID_FULL', 'PART_PAID', 'PTP', 'PART_PAID_PTP', 'BROKEN_PTP', 'RTP', 'DISPUTE', 'NOT_AVAILABLE', 'ADDRESS_ISSUE', 'DECEASED', 'REVISIT',)),
)


def _literal(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def upgrade() -> None:
    for s in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    # btree_gist backs the EXCLUDE constraints of v2_0004 (§4); trusted since PG13.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")      # gen_random_uuid() on PG < 13
    for name, values in ENUMS:
        op.execute(f"CREATE TYPE public.{name} AS ENUM ({', '.join(_literal(v) for v in values)})")


def downgrade() -> None:
    for name, _ in reversed(ENUMS):
        op.execute(f"DROP TYPE IF EXISTS public.{name}")
    for s in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
