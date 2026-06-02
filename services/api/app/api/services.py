"""Services endpoints — list and edit service overrides from the dashboard.

GET    /v1/workspaces/{slug}/services                   → list services (calendar + manual)
POST   /v1/workspaces/{slug}/services                   → add a manual service
PATCH  /v1/workspaces/{slug}/services/{service_id:path} → save name/description/price override
DELETE /v1/workspaces/{slug}/services/{service_id:path} → delete a manual service
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import get_current_user_optional
from app.models import Workspace
from app.models.workspace import WorkspaceOwner
from app.services.calendar.registry import get_provider_for_workspace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/workspaces", tags=["services"])


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


class ServiceItem(BaseModel):
    id: str
    name: str
    description: str
    duration_minutes: int
    price: str
    booking_url: str | None = None
    is_manual: bool = False


class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: str | None = None


class ServiceCreate(BaseModel):
    name: str
    description: str = ""
    price: str = ""
    duration_minutes: int = 60


@router.get("/{slug}/services", response_model=list[ServiceItem])
async def list_services(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """List services from the connected calendar merged with any dashboard overrides, plus manual services."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)
    overrides: dict = workspace.services_config or {}

    # Fetch calendar-backed services
    calendar_services = []
    try:
        provider = await get_provider_for_workspace(workspace, db)
        if provider:
            calendar_services = await provider.list_services()
    except Exception as e:
        logger.warning("Could not fetch services from provider for %s: %s", slug, e)

    # Collect IDs of calendar-backed services to avoid duplicating manual entries
    cal_ids = {s.id for s in calendar_services}

    result = [
        ServiceItem(
            id=s.id,
            name=overrides.get(s.id, {}).get("name") or s.name,
            description=overrides.get(s.id, {}).get("description") or s.description or "",
            duration_minutes=s.duration_minutes,
            price=overrides.get(s.id, {}).get("price", ""),
            booking_url=s.booking_url,
            is_manual=False,
        )
        for s in calendar_services
    ]

    # Append manually-created services (those with _manual=True in services_config)
    for svc_id, entry in overrides.items():
        if entry.get("_manual") and svc_id not in cal_ids:
            result.append(ServiceItem(
                id=svc_id,
                name=entry.get("name", ""),
                description=entry.get("description", ""),
                duration_minutes=entry.get("duration_minutes", 60),
                price=entry.get("price", ""),
                booking_url=None,
                is_manual=True,
            ))

    return result


@router.post("/{slug}/services", response_model=ServiceItem, status_code=201)
async def create_service(
    slug: str,
    payload: ServiceCreate,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Create a manual service (not backed by a calendar event type)."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    svc_id = f"manual-{uuid.uuid4()}"
    overrides = dict(workspace.services_config or {})
    overrides[svc_id] = {
        "_manual": True,
        "name": payload.name,
        "description": payload.description,
        "price": payload.price,
        "duration_minutes": payload.duration_minutes,
    }
    workspace.services_config = overrides
    await db.commit()

    return ServiceItem(
        id=svc_id,
        name=payload.name,
        description=payload.description,
        duration_minutes=payload.duration_minutes,
        price=payload.price,
        booking_url=None,
        is_manual=True,
    )


@router.patch("/{slug}/services/{service_id:path}", response_model=ServiceItem)
async def update_service(
    slug: str,
    service_id: str,
    payload: ServiceUpdate,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Save display overrides (name, description, price) for one service."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    overrides = dict(workspace.services_config or {})
    entry = dict(overrides.get(service_id, {}))

    if payload.name is not None:
        entry["name"] = payload.name
    if payload.description is not None:
        entry["description"] = payload.description
    if payload.price is not None:
        entry["price"] = payload.price

    overrides[service_id] = entry
    workspace.services_config = overrides
    await db.commit()

    is_manual = bool(entry.get("_manual"))

    if is_manual:
        return ServiceItem(
            id=service_id,
            name=entry.get("name", ""),
            description=entry.get("description", ""),
            duration_minutes=entry.get("duration_minutes", 60),
            price=entry.get("price", ""),
            booking_url=None,
            is_manual=True,
        )

    # Re-fetch from provider to return authoritative duration / booking_url
    calendar_service = None
    try:
        provider = await get_provider_for_workspace(workspace, db)
        if provider:
            services = await provider.list_services()
            calendar_service = next((s for s in services if s.id == service_id), None)
    except Exception:
        pass

    return ServiceItem(
        id=service_id,
        name=entry.get("name") or (calendar_service.name if calendar_service else ""),
        description=entry.get("description") or (calendar_service.description if calendar_service else "") or "",
        duration_minutes=calendar_service.duration_minutes if calendar_service else 0,
        price=entry.get("price", ""),
        booking_url=calendar_service.booking_url if calendar_service else None,
        is_manual=False,
    )


@router.delete("/{slug}/services/{service_id:path}", status_code=204)
async def delete_service(
    slug: str,
    service_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Delete a manually-created service. Only works for manual services."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    overrides = dict(workspace.services_config or {})
    entry = overrides.get(service_id)

    if not entry or not entry.get("_manual"):
        raise HTTPException(400, "Only manually-created services can be deleted")

    del overrides[service_id]
    workspace.services_config = overrides
    await db.commit()
