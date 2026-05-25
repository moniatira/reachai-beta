"""add calendar_connections table and migrate existing calendly_tokens

Revision ID: 005_calendar_connections
Revises: 004_extracted_info
Create Date: 2026-05-25 04:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "005_calendar_connections"
down_revision = "004_extracted_info"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add primary_calendar_provider to workspaces
    op.add_column(
        "workspaces",
        sa.Column("primary_calendar_provider", sa.String(20), nullable=True),
    )

    # 2. Create unified calendar_connections table
    op.create_table(
        "calendar_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("access_token_enc", sa.Text, nullable=False),
        sa.Column("refresh_token_enc", sa.Text, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("account_email", sa.String(200), nullable=True),
        sa.Column("account_id", sa.String(200), nullable=True),
        sa.Column("provider_metadata", sa.JSON, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "provider", name="uq_workspace_provider"),
    )
    op.create_index("ix_calendar_connections_workspace_id", "calendar_connections", ["workspace_id"])
    op.create_index("ix_calendar_connections_workspace_provider", "calendar_connections", ["workspace_id", "provider"])

    # 3. Migrate existing calendly_tokens into calendar_connections.
    #    Workspaces with a Calendly token get a corresponding row with
    #    provider='calendly' and their primary_calendar_provider set.
    op.execute("""
        INSERT INTO calendar_connections (
            id, workspace_id, provider, access_token_enc, refresh_token_enc,
            expires_at, account_email, account_id, provider_metadata,
            active, created_at, updated_at
        )
        SELECT
            gen_random_uuid()::text,
            workspace_id,
            'calendly',
            access_token_enc,
            refresh_token_enc,
            expires_at,
            calendly_email,
            calendly_user_uri,
            json_build_object('scheduling_url', scheduling_url),
            true,
            created_at,
            updated_at
        FROM calendly_tokens
        WHERE NOT EXISTS (
            SELECT 1 FROM calendar_connections cc
            WHERE cc.workspace_id = calendly_tokens.workspace_id
              AND cc.provider = 'calendly'
        )
    """)

    # 4. Set primary_calendar_provider='calendly' for migrated workspaces
    op.execute("""
        UPDATE workspaces
        SET primary_calendar_provider = 'calendly'
        WHERE id IN (
            SELECT workspace_id FROM calendly_tokens
        )
        AND primary_calendar_provider IS NULL
    """)


def downgrade():
    op.drop_index("ix_calendar_connections_workspace_provider", "calendar_connections")
    op.drop_index("ix_calendar_connections_workspace_id", "calendar_connections")
    op.drop_table("calendar_connections")
    op.drop_column("workspaces", "primary_calendar_provider")
