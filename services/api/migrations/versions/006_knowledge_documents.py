"""add knowledge_documents table

Revision ID: 006_knowledge_documents
Revises: 005_calendar_connections
Create Date: 2026-06-01 00:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "006_knowledge_documents"
down_revision = "005_calendar_connections"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_name", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("char_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_knowledge_documents_workspace_id",
        "knowledge_documents",
        ["workspace_id"],
    )


def downgrade():
    op.drop_index("ix_knowledge_documents_workspace_id", "knowledge_documents")
    op.drop_table("knowledge_documents")
