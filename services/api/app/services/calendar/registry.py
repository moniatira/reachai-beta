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
    """Return the default calendar provider for this workspace, or None if none connected.

    Preference order:
      1. workspace.primary_calendar_provider (explicit choice)
      2. Most recently connected active CalendarConnection (fallback)
    """
    settings = get_settings()

    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace.id,
            CalendarConnection.active.is_(True),
        ).order_by(CalendarConnection.created_at.desc())
    )
    connections = result.scalars().all()

    if not connections:
        return None

    preferred = workspace.primary_calendar_provider
    connection = None
    if preferred:
        connection = next((c for c in connections if c.provider == preferred), None)
    if connection is None:
        connection = connections[0]

    return _instantiate(connection, settings, db=db)


async def get_provider_by_connection_id(
    connection_id: str, db: AsyncSession
) -> CalendarProvider | None:
    """Return the provider for a specific CalendarConnection ID.

    Used when booking with a specific staff member — the connection_id
    comes from list_staff / find_available_slots slot metadata.
    """
    settings = get_settings()
    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.id == connection_id,
            CalendarConnection.active.is_(True),
        )
    )
    connection = result.scalar_one_or_none()
    if not connection:
        return None
    return _instantiate(connection, settings, db=db)


async def get_all_providers_for_workspace(
    workspace: Workspace, db: AsyncSession
) -> list[tuple[CalendarConnection, CalendarProvider]]:
    """Return all active (connection, provider) pairs for a workspace.

    Used to aggregate availability across multiple staff members.
    Returns pairs sorted by staff_name (nulls last), then created_at.
    """
    settings = get_settings()
    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace.id,
            CalendarConnection.active.is_(True),
        ).order_by(CalendarConnection.staff_name.nullslast(), CalendarConnection.created_at)
    )
    connections = result.scalars().all()

    pairs = []
    for conn in connections:
        try:
            provider = _instantiate(conn, settings, db=db)
            pairs.append((conn, provider))
        except CalendarProviderError as e:
            logger.warning("Skipping connection %s: %s", conn.id, e)
    return pairs


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
        ).order_by(CalendarConnection.staff_name.nullslast(), CalendarConnection.created_at)
    )
    return list(result.scalars().all())
