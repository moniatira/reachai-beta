"""SQLAlchemy ORM models for the ReachAI beta."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    return str(uuid.uuid4())


class Workspace(Base):
    """An SMB tenant. Slug appears in the embed code: data-workspace='acme-salon'."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(80), nullable=True)
    owner_email: Mapped[str] = mapped_column(String(200))

    assistant_name: Mapped[str] = mapped_column(String(80), default="Sarah")
    greeting: Mapped[str] = mapped_column(
        Text,
        default="Hi! I'm Sarah, the booking assistant. How can I help today?",
    )
    tone: Mapped[str] = mapped_column(String(40), default="warm")

    brand_primary: Mapped[str] = mapped_column(String(20), default="#534AB7")
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    calendly_token: Mapped["CalendlyToken | None"] = relationship(
        back_populates="workspace", uselist=False, cascade="all, delete-orphan"
    )
    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    bookings: Mapped[list["Booking"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class CalendlyToken(Base):
    """Encrypted Calendly OAuth tokens for a workspace."""

    __tablename__ = "calendly_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True
    )

    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    calendly_user_uri: Mapped[str] = mapped_column(String(500))
    calendly_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scheduling_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    workspace: Mapped[Workspace] = relationship(back_populates="calendly_token")


class ChatSession(Base):
    """A single chat conversation. Messages stored as JSON list."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(20), default="chat")

    messages: Mapped[list] = mapped_column(JSON, default=list)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    booked: Mapped[bool] = mapped_column(Boolean, default=False)
    ended: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    workspace: Mapped[Workspace] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_chat_sessions_workspace_created", "workspace_id", "created_at"),)


class Booking(Base):
    """A confirmed booking that landed on the SMB's Calendly."""

    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="chat")

    customer_name: Mapped[str] = mapped_column(String(200))
    customer_email: Mapped[str] = mapped_column(String(200))
    customer_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

    event_type_uri: Mapped[str] = mapped_column(String(500))
    event_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    service_name: Mapped[str] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    workspace: Mapped[Workspace] = relationship(back_populates="bookings")

    __table_args__ = (Index("ix_bookings_workspace_scheduled", "workspace_id", "scheduled_for"),)
