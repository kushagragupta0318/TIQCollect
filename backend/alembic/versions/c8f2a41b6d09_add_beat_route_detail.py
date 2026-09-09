"""add route detail and actuals to beats

Revision ID: c8f2a41b6d09
Revises: b3e7d1f90c25
Create Date: 2026-09-08

Five columns on `beats`:

    route_geometry            encoded polyline from OSRM /route
    route_legs                per-leg seconds and metres, as the solver had them
    route_source              "osrm" | "haversine" — which provider actually answered
    actual_distance_km        reconciled from the agent's GPS trail
    actual_duration_minutes   reconciled from first check-in to last check-out

WHY. `estimated_distance_km` and `estimated_duration_minutes` already existed and
were computed by planner_service as Haversine x 1.15, immediately after
core/routing had fetched a real OSRM road matrix and discarded it. There was also
nothing to compare an estimate against, so no routing change could be shown to
have improved anything and a travel-time model had no label to learn from.

NOTE ON THE TWO SCHEMA AUTHORITIES (CLAUDE.md known issue 5). seed_data.py does
drop_all + create_all and never stamps alembic_version, so a seeded box builds
these columns from app/models/beat.py and never runs this migration, while a
production box runs this and never runs the seed. Both paths have to stay
correct; this file and the model were written together and must change together.
"""
from alembic import op
import sqlalchemy as sa

revision = "c8f2a41b6d09"
down_revision = "b3e7d1f90c25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("beats", sa.Column("route_geometry", sa.Text(), nullable=True))
    op.add_column("beats", sa.Column("route_legs", sa.JSON(), nullable=True))
    op.add_column("beats", sa.Column("route_source", sa.String(length=20), nullable=True))
    op.add_column("beats", sa.Column("actual_distance_km", sa.Float(), nullable=True))
    op.add_column("beats", sa.Column("actual_duration_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("beats", "actual_duration_minutes")
    op.drop_column("beats", "actual_distance_km")
    op.drop_column("beats", "route_source")
    op.drop_column("beats", "route_legs")
    op.drop_column("beats", "route_geometry")
