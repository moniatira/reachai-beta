"""Calendar status endpoint — list connections and check health.

GET  /v1/calendar/status/{slug}      → list all connections + health
POST /v1/calendar/primary/{slug}     → set the primary provider
DELETE /v1/calendar/{slug}/{provider} → disconnect a provider
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import get_current_user_optional
from app.models import Workspace
from app.models.calendar_connection import CalendarConnection
from app.models.workspace import WorkspaceOwner
from app.services.calendar import PROVIDER_NAMES, list_connections_for_workspace
from app.services.calendar.registry import _instantiate


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/calendar", tags=["calendar"])


async def _load_workspace_owner_or_admin(
    slug: str, db: AsyncSession, x_admin_key: str | None, user_id: str | None
) -> Workspace:
    settings = get_settings()
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")

    is_admin = x_admin_key and x_admin_key == settings.admin_api_key
    if is_admin:
        return workspace

    if not user_id:
        raise HTTPException(401, "Admin key or user session required")

    owner_check = await db.execute(
        select(WorkspaceOwner).where(
            WorkspaceOwner.workspace_id == workspace.id,
            WorkspaceOwner.user_id == user_id,
        )
    )
    if not owner_check.scalar_one_or_none():
        raise HTTPException(403, "You don't have access to this workspace")

    return workspace


class ConnectionSummary(BaseModel):
    provider: str
    account_email: str | None
    is_primary: bool
    healthy: bool
    connect_url: str
    disconnect_url: str


class CalendarStatusResponse(BaseModel):
    slug: str
    primary_provider: str | None
    connections: list[ConnectionSummary]
    available_providers: list[str]


@router.get("/status/{slug}", response_model=CalendarStatusResponse)
async def calendar_status(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """List all calendar connections for a workspace + their health."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)
    settings = get_settings()
    api_base = settings.calendly_redirect_uri.rsplit("/v1/", 1)[0]

    connections = await list_connections_for_workspace(workspace.id, db)
    summaries = []
    for conn in connections:
        try:
            provider = _instantiate(conn, settings, db)
            healthy = await provider.health_check()
        except Exception as e:
            logger.warning("Health check failed for %s: %s", conn.provider, e)
            healthy = False

        summaries.append(ConnectionSummary(
            provider=conn.provider,
            account_email=conn.account_email,
            is_primary=(conn.provider == workspace.primary_calendar_provider),
            healthy=healthy,
            connect_url=f"{api_base}/v1/{conn.provider}/connect/{slug}",
            disconnect_url=f"{api_base}/v1/calendar/{slug}/{conn.provider}",
        ))

    return CalendarStatusResponse(
        slug=slug,
        primary_provider=workspace.primary_calendar_provider,
        connections=summaries,
        available_providers=sorted(PROVIDER_NAMES),
    )


class SetPrimaryRequest(BaseModel):
    provider: str


@router.post("/primary/{slug}")
async def set_primary_provider(
    slug: str,
    payload: SetPrimaryRequest,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Switch the workspace's primary calendar provider."""
    if payload.provider not in PROVIDER_NAMES:
        raise HTTPException(400, f"provider must be one of {sorted(PROVIDER_NAMES)}")

    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    # Verify the connection exists
    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace.id,
            CalendarConnection.provider == payload.provider,
            CalendarConnection.active.is_(True),
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(400, f"No active {payload.provider} connection for this workspace")

    workspace.primary_calendar_provider = payload.provider
    await db.commit()
    return {"ok": True, "primary_provider": payload.provider}


@router.delete("/{slug}/{provider}")
async def disconnect_provider(
    slug: str,
    provider: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Disconnect a calendar provider from a workspace."""
    if provider not in PROVIDER_NAMES:
        raise HTTPException(400, f"provider must be one of {sorted(PROVIDER_NAMES)}")

    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace.id,
            CalendarConnection.provider == provider,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, f"No {provider} connection found")

    await db.delete(conn)

    # If this was the primary provider, clear it (or fall back to next available)
    if workspace.primary_calendar_provider == provider:
        remaining = await db.execute(
            select(CalendarConnection).where(
                CalendarConnection.workspace_id == workspace.id,
                CalendarConnection.provider != provider,
                CalendarConnection.active.is_(True),
            )
        )
        next_conn = remaining.scalars().first()
        workspace.primary_calendar_provider = next_conn.provider if next_conn else None

    await db.commit()
    return {"ok": True, "disconnected": provider}
