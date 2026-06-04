"""Multi-staff support: staff_name on calendar_connections, staff fields on bookings.

Revision ID: 008_multi_staff
Revises: 007_services_config
Create Date: 2026-06-04 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "008_multi_staff"
down_revision = "007_services_config"
branch_labels = None
depends_on = None


def upgrade():
    # Add staff_name to calendar_connections
    op.add_column(
        "calendar_connections",
        sa.Column("staff_name", sa.String(200), nullable=True),
    )

    # Drop the old (workspace_id, provider) unique constraint —
    # a salon can have multiple Google/Calendly connections, one per stylist
    op.drop_constraint("uq_workspace_provider", "calendar_connections", type_="unique")
    op.drop_index("ix_calendar_connections_workspace_provider", "calendar_connections")

    # New constraint: same calendar account can't be added twice to same workspace
    op.create_unique_constraint(
        "uq_workspace_provider_account",
        "calendar_connections",
        ["workspace_id", "provider", "account_id"],
    )
    op.create_index(
        "ix_calendar_connections_workspace",
        "calendar_connections",
        ["workspace_id"],
    )

    # Add staff fields to bookings
    op.add_column(
        "bookings",
        sa.Column("staff_name", sa.String(200), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("calendar_connection_id", sa.String(36), nullable=True),
    )


def downgrade():
    op.drop_column("bookings", "calendar_connection_id")
    op.drop_column("bookings", "staff_name")

    op.drop_constraint("uq_workspace_provider_account", "calendar_connections", type_="unique")
    op.drop_index("ix_calendar_connections_workspace", "calendar_connections")

    op.create_unique_constraint(
        "uq_workspace_provider", "calendar_connections", ["workspace_id", "provider"]
    )
    op.create_index(
        "ix_calendar_connections_workspace_provider",
        "calendar_connections",
        ["workspace_id", "provider"],
    )

    op.drop_column("calendar_connections", "staff_name")
