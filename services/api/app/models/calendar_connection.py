"""Unified calendar connection model — supports Calendly, Google, and Outlook.

Replaces the Calendly-only `calendly_tokens` table. Existing Calendly rows
are migrated into this table by migration 005.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    return str(uuid.uuid4())


VALID_PROVIDERS = {"calendly", "google", "outlook"}


class CalendarConnection(Base):
    """One row per (workspace, provider) combo.

    A workspace can theoretically have multiple connections (e.g., both
    Calendly AND Google), but `workspace.primary_calendar_provider` is the
    one Sarah uses by default.
    """

    __tablename__ = "calendar_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    workspace_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    # "calendly" | "google" | "outlook"

    # Encrypted OAuth tokens
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Account identity
    account_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # For Calendly: user_uri; for Google: numeric ID; for Outlook: Graph object ID

    # Provider-specific config (calendar IDs, services list, prefs)
    connection_metadata: Mapped[dict | None] = mapped_column("provider_metadata", JSON, nullable=True)

    # Human-readable label for the staff member this calendar belongs to (e.g. "Aisha")
    staff_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        # Same calendar account can't be added twice to the same workspace,
        # but multiple different accounts (stylists) can share a provider.
        UniqueConstraint("workspace_id", "provider", "account_id", name="uq_workspace_provider_account"),
        Index("ix_calendar_connections_workspace", "workspace_id"),
    )
