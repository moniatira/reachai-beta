"""Provider registry — workspace → CalendarProvider factory.

The chat code calls `get_provider_for_workspace(workspace, db)` and gets back
the right CalendarProvider instance. Doesn't need to know which type.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Workspace
from app.models.calendar_connection import CalendarConnection
from app.services.calendar.base import CalendarProvider, CalendarProviderError
from app.services.calendar.calendly_provider import CalendlyProvider
from app.services.calendar.google_provider import GoogleCalendarProvider
from app.services.calendar.outlook_provider import OutlookCalendarProvider


logger = logging.getLogger(__name__)


PROVIDER_NAMES = {"calendly", "google", "outlook"}


async def get_provider_for_workspace(
    workspace: Workspace, db: AsyncSession
) -> CalendarProvider | None:
    """Return the calendar provider for this workspace, or None if no calendar
    is connected.

    Order of preference:
      1. workspace.primary_calendar_provider (explicit choice)
      2. First active CalendarConnection on the workspace (fallback)
      3. Legacy CalendlyToken (for workspaces predating Day 3 migration)
    """
    settings = get_settings()

    # Load all calendar connections for this workspace, newest first
    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace.id,
            CalendarConnection.active.is_(True),
        ).order_by(CalendarConnection.created_at.desc())
    )
    connections = result.scalars().all()

    if not connections:
        return None

    # Prefer workspace's explicit primary provider; fall back to most recently connected
    preferred = workspace.primary_calendar_provider
    connection = None
    if preferred:
        connection = next((c for c in connections if c.provider == preferred), None)
    if connection is None:
        connection = connections[0]

    return _instantiate(connection, settings, db=db)


def _instantiate(
    connection: CalendarConnection,
    settings,
    db: AsyncSession | None = None,
) -> CalendarProvider:
    """Build the right provider instance for this connection."""
    if connection.provider == "google":
        if not settings.google_client_id or not settings.google_client_secret:
            raise CalendarProviderError("Google OAuth not configured on server")
        return GoogleCalendarProvider(
            connection=connection,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )

    if connection.provider == "outlook":
        if not settings.outlook_client_id or not settings.outlook_client_secret:
            raise CalendarProviderError("Outlook OAuth not configured on server")
        return OutlookCalendarProvider(
            connection=connection,
            client_id=settings.outlook_client_id,
            client_secret=settings.outlook_client_secret,
            tenant_id=settings.outlook_tenant_id or "common",
        )

    if connection.provider == "calendly":
        return CalendlyProvider(connection=connection, db=db)

    raise CalendarProviderError(f"Unknown calendar provider: {connection.provider}")


async def list_connections_for_workspace(
    workspace_id: str, db: AsyncSession
) -> list[CalendarConnection]:
    """Return all active connections for a workspace (used by dashboard)."""
    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace_id,
            CalendarConnection.active.is_(True),
        ).order_by(CalendarConnection.created_at)
    )
    return list(result.scalars().all())
