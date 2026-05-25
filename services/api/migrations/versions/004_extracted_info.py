"""add extracted_business_info JSON column to workspaces

Revision ID: 004_extracted_info
Revises: 003_workspace_owners
Create Date: 2026-05-25 03:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "004_extracted_info"
down_revision = "003_workspace_owners"
branch_labels = None
depends_on = None


def upgrade():
    # Use JSON (not JSONB) for SQLite/Postgres portability.
    # On Postgres this lands as JSONB automatically via the dialect.
    op.add_column(
        "workspaces",
        sa.Column("extracted_business_info", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("workspaces", "extracted_business_info")
