"""add services_config JSON column to workspaces

Revision ID: 007_services_config
Revises: 006_knowledge_documents
Create Date: 2026-06-02 00:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "007_services_config"
down_revision = "006_knowledge_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workspaces",
        sa.Column("services_config", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_column("workspaces", "services_config")
