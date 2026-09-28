"""UNIQUE on tenancy.agency_documents.storage_key (D02, for ce).

One stored object is one document row. The onboarding wizard's confirm step
checks for an existing row first; two concurrent confirms of the same upload
can both pass that check, and this constraint makes the second INSERT fail
(ce's confirm_document catches the IntegrityError).

Revision ID: v2_0014
Revises: v2_0013
Create Date: 2026-09-28
"""
from alembic import op

revision = "v2_0014"
down_revision = "v2_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(op.f("uq_agency_documents_storage_key"), "agency_documents", ["storage_key"],
                                schema="tenancy")


def downgrade() -> None:
    op.drop_constraint(op.f("uq_agency_documents_storage_key"), "agency_documents", schema="tenancy",
                       type_="unique")
