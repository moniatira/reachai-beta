"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-05-20 00:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(80), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("industry", sa.String(80), nullable=True),
        sa.Column("owner_email", sa.String(200), nullable=False),
        sa.Column("assistant_name", sa.String(80), nullable=False, server_default="Sarah"),
        sa.Column("greeting", sa.Text, nullable=False),
        sa.Column("tone", sa.String(40), nullable=False, server_default="warm"),
        sa.Column("brand_primary", sa.String(20), nullable=False, server_default="#534AB7"),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("whitelisted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"], unique=True)

    op.create_table(
        "calendly_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("access_token_enc", sa.Text, nullable=False),
        sa.Column("refresh_token_enc", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calendly_user_uri", sa.String(500), nullable=False),
        sa.Column("calendly_email", sa.String(200), nullable=True),
        sa.Column("scheduling_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False, server_default="chat"),
        sa.Column("messages", sa.JSON, nullable=False),
        sa.Column("customer_name", sa.String(200), nullable=True),
        sa.Column("customer_email", sa.String(200), nullable=True),
        sa.Column("customer_phone", sa.String(40), nullable=True),
        sa.Column("booked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ended", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_chat_sessions_workspace_created", "chat_sessions", ["workspace_id", "created_at"])

    op.create_table(
        "bookings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False, server_default="chat"),
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("customer_email", sa.String(200), nullable=False),
        sa.Column("customer_phone", sa.String(40), nullable=True),
        sa.Column("event_type_uri", sa.String(500), nullable=False),
        sa.Column("event_uri", sa.String(500), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bookings_workspace_scheduled", "bookings", ["workspace_id", "scheduled_for"])


def downgrade():
    op.drop_table("bookings")
    op.drop_table("chat_sessions")
    op.drop_table("calendly_tokens")
    op.drop_table("workspaces")
