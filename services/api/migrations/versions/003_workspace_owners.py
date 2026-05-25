"""add workspace_owners table and onboarding/trial fields on workspaces

Revision ID: 003_workspace_owners
Revises: 002_add_users
Create Date: 2026-05-25 00:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "003_workspace_owners"
down_revision = "002_add_users"
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to workspaces
    op.add_column(
        "workspaces",
        sa.Column("owner_user_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column("website_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "onboarding_step",
            sa.String(20),
            nullable=False,
            server_default="not_started",
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column(
            "trial_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "workspaces",
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_workspaces_owner_user_id", "workspaces", ["owner_user_id"])

    op.create_foreign_key(
        "fk_workspaces_owner_user",
        "workspaces",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Backfill existing workspaces: they predate self-serve so they're "complete"
    # in onboarding and don't need a trial timer.
    op.execute(
        "UPDATE workspaces "
        "SET onboarding_step = 'complete', trial_status = 'active' "
        "WHERE whitelisted = true"
    )

    # Create the workspace_owners bridge table
    op.create_table(
        "workspace_owners",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_owner_user_workspace"),
    )
    op.create_index("ix_workspace_owners_user_id", "workspace_owners", ["user_id"])
    op.create_index("ix_workspace_owners_workspace_id", "workspace_owners", ["workspace_id"])


def downgrade():
    op.drop_table("workspace_owners")
    op.drop_constraint("fk_workspaces_owner_user", "workspaces", type_="foreignkey")
    op.drop_index("ix_workspaces_owner_user_id", "workspaces")
    op.drop_column("workspaces", "trial_ends_at")
    op.drop_column("workspaces", "trial_status")
    op.drop_column("workspaces", "onboarding_step")
    op.drop_column("workspaces", "website_url")
    op.drop_column("workspaces", "owner_user_id")
