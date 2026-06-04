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
from app.models.workspace import WorkspaceOwner, CalendlyToken
from app.services.calendar import PROVIDER_NAMES, list_connections_for_workspace
from app.services.calendar.registry import _instantiate
from app.services.calendly import _api_get
from app.core.security import decrypt_token


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
    connection_id: str
    provider: str
    account_email: str | None
    staff_name: str | None
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
            connection_id=conn.id,
            provider=conn.provider,
            account_email=conn.account_email,
            staff_name=conn.staff_name,
            is_primary=(conn.provider == workspace.primary_calendar_provider),
            healthy=healthy,
            connect_url=f"{api_base}/v1/{conn.provider}/connect/{slug}",
            disconnect_url=f"{api_base}/v1/calendar/{slug}/connection/{conn.id}",
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
    conns = result.scalars().all()

    # For Calendly, also wipe the CalendlyToken so next OAuth shows fresh consent.
    # Do this before the 404 check — the token may be orphaned (conn already deleted).
    if provider == "calendly":
        token_result = await db.execute(
            select(CalendlyToken).where(CalendlyToken.workspace_id == workspace.id)
        )
        token = token_result.scalar_one_or_none()
        if token:
            await db.delete(token)

    if not conns:
        # Token-only cleanup path — commit what we deleted and return success
        if workspace.primary_calendar_provider == provider:
            workspace.primary_calendar_provider = None
        await db.commit()
        return {"ok": True, "disconnected": provider, "note": "no CalendarConnection found, CalendlyToken cleared"}

    for conn in conns:
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


@router.delete("/{slug}/connection/{connection_id}")
async def disconnect_connection(
    slug: str,
    connection_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Disconnect a specific calendar connection by ID (supports multi-staff)."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.id == connection_id,
            CalendarConnection.workspace_id == workspace.id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")

    provider = conn.provider
    await db.delete(conn)

    # If this was the primary, fall back to next available connection
    if workspace.primary_calendar_provider == provider:
        remaining = await db.execute(
            select(CalendarConnection).where(
                CalendarConnection.workspace_id == workspace.id,
                CalendarConnection.active.is_(True),
            ).order_by(CalendarConnection.created_at.desc())
        )
        next_conn = remaining.scalars().first()
        workspace.primary_calendar_provider = next_conn.provider if next_conn else None

    await db.commit()
    return {"ok": True, "disconnected": connection_id}


@router.get("/debug-event-types/{slug}")
async def debug_event_types(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
):
    """Admin-only: fetch raw Calendly event types including full location config."""
    settings = get_settings()
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(403, "Admin key required")

    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")

    conn_result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace.id,
            CalendarConnection.provider == "calendly",
            CalendarConnection.active.is_(True),
        ).limit(1)
    )
    conn = conn_result.scalars().first()
    if not conn:
        raise HTTPException(404, "No active Calendly connection for this workspace")

    access_token = decrypt_token(conn.access_token_enc)
    data = await _api_get(access_token, "/event_types", params={"user": conn.account_id, "active": "true"})

    # For each event type, fetch full detail including locations
    detailed = []
    for et in data.get("collection", []):
        from urllib.parse import urlparse
        et_path = urlparse(et["uri"]).path
        try:
            detail = await _api_get(access_token, et_path)
            resource = detail.get("resource", et)
        except Exception:
            resource = et
        detailed.append({
            "uri": et["uri"],
            "name": et["name"],
            "duration": et.get("duration"),
            "locations": resource.get("locations", []),
            "scheduling_url": et.get("scheduling_url"),
        })

    return {"event_types": detailed}
