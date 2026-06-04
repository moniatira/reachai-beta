"""Workspace management endpoints.

Day 2 changes:
  - POST /v1/workspaces now accepts either X-Admin-Key OR session JWT.
    Admin path remains for whitelist.py; user path is the new self-serve route.
  - NEW: GET /v1/workspaces/me — list workspaces owned by current user
"""
import re
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import get_current_user_optional
from app.models import Workspace
from app.models.user import User
from app.models.workspace import BUSINESS, PENDING, WorkspaceOwner, ChatSession, Booking, KnowledgeDocument
from app.services.calendly import build_authorize_url


router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


class WorkspaceCreate(BaseModel):
    slug: str = Field(..., description="URL-safe identifier")
    name: str = Field(..., min_length=2, max_length=200)
    owner_email: EmailStr | None = None
    industry: str | None = None
    assistant_name: str = "Sarah"
    tone: str = "warm"
    brand_primary: str = "#534AB7"


class WorkspaceResponse(BaseModel):
    id: str
    slug: str
    name: str
    owner_email: str
    whitelisted: bool
    calendly_connected: bool
    calendly_connect_url: str
    embed_code: str
    onboarding_step: str
    trial_status: str
    assistant_name: str | None = None
    greeting: str | None = None
    tone: str | None = None
    brand_primary: str | None = None


class WorkspaceMeListItem(BaseModel):
    id: str
    slug: str
    name: str
    onboarding_step: str
    trial_status: str
    calendly_connected: bool
    embed_code: str


@router.post("", response_model=WorkspaceResponse)
async def create_workspace(
    payload: WorkspaceCreate,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Create a workspace.

    Accepts EITHER:
      - X-Admin-Key header (legacy whitelist.py path)
      - Session JWT (Authorization: Bearer …)  ← new self-serve path

    If session JWT is used, the workspace is automatically owned by the user.
    """
    settings = get_settings()
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key

    if not is_admin and not user_id:
        raise HTTPException(401, "Missing admin key or user session")

    if not SLUG_RE.match(payload.slug):
        raise HTTPException(400, "Slug must be 3-80 chars: lowercase letters, digits, hyphens")

    existing = await db.execute(select(Workspace).where(Workspace.slug == payload.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Slug '{payload.slug}' is taken")

    # Determine owner email
    if user_id:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "User not found")
        owner_email = payload.owner_email or user.email
        owner_user_id = user.id
    else:
        if not payload.owner_email:
            raise HTTPException(400, "owner_email required for admin-key creation")
        owner_email = str(payload.owner_email)
        owner_user_id = None

    workspace = Workspace(
        slug=payload.slug,
        name=payload.name,
        owner_email=owner_email,
        owner_user_id=owner_user_id,
        industry=payload.industry,
        assistant_name=payload.assistant_name,
        tone=payload.tone,
        brand_primary=payload.brand_primary,
        whitelisted=True,
        onboarding_step=BUSINESS,
        trial_status=PENDING,
    )
    db.add(workspace)
    await db.flush()

    # If user-authed, register ownership
    if owner_user_id:
        db.add(WorkspaceOwner(user_id=owner_user_id, workspace_id=workspace.id, role="owner"))

    await db.commit()
    await db.refresh(workspace)

    api_base = settings.calendly_redirect_uri.rsplit("/v1/", 1)[0]
    return WorkspaceResponse(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        owner_email=workspace.owner_email,
        whitelisted=workspace.whitelisted,
        calendly_connected=False,
        calendly_connect_url=build_authorize_url(workspace.slug),
        embed_code=f'<script src="{api_base}/v1/widget/{workspace.slug}.js" async></script>',
        onboarding_step=workspace.onboarding_step,
        trial_status=workspace.trial_status,
        assistant_name=workspace.assistant_name,
        greeting=workspace.greeting,
        tone=workspace.tone,
        brand_primary=workspace.brand_primary,
    )


@router.get("/me", response_model=list[WorkspaceMeListItem])
async def list_my_workspaces(
    user_id: str = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Return all workspaces owned by the current authenticated user."""
    if not user_id:
        raise HTTPException(401, "Authentication required")

    settings = get_settings()
    api_base = settings.calendly_redirect_uri.rsplit("/v1/", 1)[0]

    result = await db.execute(
        select(Workspace)
        .join(WorkspaceOwner, WorkspaceOwner.workspace_id == Workspace.id)
        .where(WorkspaceOwner.user_id == user_id)
        .order_by(Workspace.created_at.desc())
    )
    workspaces = result.scalars().all()

    items = []
    for w in workspaces:
        await db.refresh(w, ["calendly_token"])
        items.append(
            WorkspaceMeListItem(
                id=w.id,
                slug=w.slug,
                name=w.name,
                onboarding_step=w.onboarding_step,
                trial_status=w.trial_status,
                calendly_connected=w.calendly_token is not None,
                embed_code=f'<script src="{api_base}/v1/widget/{w.slug}.js" async></script>',
            )
        )

    return items


@router.get("/{slug}", response_model=WorkspaceResponse)
async def get_workspace(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Get workspace details. Accessible to admin OR to the workspace's owner."""
    settings = get_settings()
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key

    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    await db.refresh(workspace, ["calendly_token"])

    if not is_admin:
        if not user_id:
            raise HTTPException(401, "Authentication required")
        owner_check = await db.execute(
            select(WorkspaceOwner).where(
                WorkspaceOwner.workspace_id == workspace.id,
                WorkspaceOwner.user_id == user_id,
            )
        )
        if not owner_check.scalar_one_or_none():
            raise HTTPException(403, "You don't have access to this workspace")

    api_base = settings.calendly_redirect_uri.rsplit("/v1/", 1)[0]
    return WorkspaceResponse(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        owner_email=workspace.owner_email,
        whitelisted=workspace.whitelisted,
        calendly_connected=workspace.calendly_token is not None,
        calendly_connect_url=build_authorize_url(workspace.slug),
        embed_code=f'<script src="{api_base}/v1/widget/{workspace.slug}.js" async></script>',
        onboarding_step=workspace.onboarding_step,
        trial_status=workspace.trial_status,
        assistant_name=workspace.assistant_name,
        greeting=workspace.greeting,
        tone=workspace.tone,
        brand_primary=workspace.brand_primary,
    )


# ── Settings PATCH ─────────────────────────────────────────────────────────

class WorkspaceSettingsUpdate(BaseModel):
    name: str | None = None
    assistant_name: str | None = None
    greeting: str | None = None
    tone: str | None = None
    brand_primary: str | None = None


@router.patch("/{slug}/settings", response_model=WorkspaceResponse)
async def update_workspace_settings(
    slug: str,
    payload: WorkspaceSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Update assistant/workspace settings. Auth: admin or workspace owner."""
    settings = get_settings()
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key

    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")

    if not is_admin:
        if not user_id:
            raise HTTPException(401, "Authentication required")
        owner_check = await db.execute(
            select(WorkspaceOwner).where(
                WorkspaceOwner.workspace_id == workspace.id,
                WorkspaceOwner.user_id == user_id,
            )
        )
        if not owner_check.scalar_one_or_none():
            raise HTTPException(403, "You don't have access to this workspace")

    if payload.name is not None:
        workspace.name = payload.name
    if payload.assistant_name is not None:
        workspace.assistant_name = payload.assistant_name
    if payload.greeting is not None:
        workspace.greeting = payload.greeting
    if payload.tone is not None:
        workspace.tone = payload.tone
    if payload.brand_primary is not None:
        workspace.brand_primary = payload.brand_primary

    await db.commit()
    await db.refresh(workspace)

    api_base = settings.calendly_redirect_uri.rsplit("/v1/", 1)[0]
    await db.refresh(workspace, ["calendly_token"])
    return WorkspaceResponse(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        owner_email=workspace.owner_email,
        whitelisted=workspace.whitelisted,
        calendly_connected=workspace.calendly_token is not None,
        calendly_connect_url=build_authorize_url(workspace.slug),
        embed_code=f'<script src="{api_base}/v1/widget/{workspace.slug}.js" async></script>',
        onboarding_step=workspace.onboarding_step,
        trial_status=workspace.trial_status,
        assistant_name=workspace.assistant_name,
        greeting=workspace.greeting,
        tone=workspace.tone,
        brand_primary=workspace.brand_primary,
    )


# ── Analytics ──────────────────────────────────────────────────────────────

class AnalyticsResponse(BaseModel):
    bookings_today: int
    bookings_this_month: int
    conversations_total: int
    conversations_with_booking: int


@router.get("/{slug}/analytics", response_model=AnalyticsResponse)
async def get_workspace_analytics(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    settings = get_settings()
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    if not is_admin:
        if not user_id:
            raise HTTPException(401, "Authentication required")
        owner_check = await db.execute(
            select(WorkspaceOwner).where(
                WorkspaceOwner.workspace_id == workspace.id,
                WorkspaceOwner.user_id == user_id,
            )
        )
        if not owner_check.scalar_one_or_none():
            raise HTTPException(403, "Access denied")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    confirmed_filter = or_(
        Booking.event_uri.is_not(None),
        Booking.session_id.is_not(None),
    )

    bookings_today = (await db.execute(
        select(func.count()).select_from(Booking).where(
            Booking.workspace_id == workspace.id,
            Booking.created_at >= today_start,
            confirmed_filter,
        )
    )).scalar() or 0

    bookings_month = (await db.execute(
        select(func.count()).select_from(Booking).where(
            Booking.workspace_id == workspace.id,
            Booking.created_at >= month_start,
            confirmed_filter,
        )
    )).scalar() or 0

    conversations_total = (await db.execute(
        select(func.count()).select_from(ChatSession).where(
            ChatSession.workspace_id == workspace.id,
        )
    )).scalar() or 0

    conversations_booked = (await db.execute(
        select(func.count()).select_from(ChatSession).where(
            ChatSession.workspace_id == workspace.id,
            ChatSession.booked == True,
        )
    )).scalar() or 0

    return AnalyticsResponse(
        bookings_today=bookings_today,
        bookings_this_month=bookings_month,
        conversations_total=conversations_total,
        conversations_with_booking=conversations_booked,
    )


# ── Appointments ───────────────────────────────────────────────────────────

class AppointmentItem(BaseModel):
    id: str
    customer_name: str
    customer_email: str
    service_name: str
    channel: str
    scheduled_for: str
    duration_minutes: int


@router.get("/{slug}/assign-owner")
async def assign_workspace_owner(
    slug: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
):
    """Admin-only: link an existing user (by email) as owner of a workspace.

    Pass owner_email as a query param: ?owner_email=user@example.com
    The user must have logged in at least once so their account exists.
    """
    settings = get_settings()
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(403, "Admin key required")

    ws_result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = ws_result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, f"Workspace '{slug}' not found")

    user_result = await db.execute(select(User).where(User.email == owner_email))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"No user with email '{owner_email}'. They must log in at least once first.")

    existing = await db.execute(
        select(WorkspaceOwner).where(
            WorkspaceOwner.workspace_id == workspace.id,
            WorkspaceOwner.user_id == user.id,
        )
    )
    if not existing.scalar_one_or_none():
        db.add(WorkspaceOwner(user_id=user.id, workspace_id=workspace.id, role="owner"))

    workspace.owner_email = owner_email
    workspace.owner_user_id = user.id

    await db.commit()
    return {"ok": True, "workspace": slug, "owner_email": owner_email}


@router.get("/{slug}/clone")
async def copy_workspace_settings(
    slug: str,
    to: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
):
    """Admin-only: copy configurable settings and knowledge docs to another workspace.

    Copies: name, industry, website_url, assistant_name, greeting, tone,
    brand_primary, logo_url, extracted_business_info, services_config,
    onboarding_step, and all KnowledgeDocuments.
    Does NOT copy: slug, id, owner, calendar connections, chat sessions, bookings.
    """
    settings = get_settings()
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(403, "Admin key required")

    src_result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    src = src_result.scalar_one_or_none()
    if not src:
        raise HTTPException(404, f"Source workspace '{slug}' not found")

    dst_result = await db.execute(select(Workspace).where(Workspace.slug == to))
    dst = dst_result.scalar_one_or_none()
    if not dst:
        raise HTTPException(404, f"Destination workspace '{to}' not found")

    source_slug = slug
    dest_slug = to

    # Copy scalar settings
    dst.name = src.name
    dst.industry = src.industry
    dst.website_url = src.website_url
    dst.assistant_name = src.assistant_name
    dst.greeting = src.greeting
    dst.tone = src.tone
    dst.brand_primary = src.brand_primary
    dst.logo_url = src.logo_url
    dst.extracted_business_info = src.extracted_business_info
    dst.services_config = src.services_config
    dst.onboarding_step = src.onboarding_step

    # Copy knowledge documents
    docs_result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == src.id)
    )
    src_docs = docs_result.scalars().all()

    # Delete existing knowledge docs on destination first
    existing_docs_result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == dst.id)
    )
    for doc in existing_docs_result.scalars().all():
        await db.delete(doc)

    # Add copies
    for doc in src_docs:
        db.add(KnowledgeDocument(
            workspace_id=dst.id,
            source_type=doc.source_type,
            source_name=doc.source_name,
            content=doc.content,
            char_count=doc.char_count,
        ))

    await db.commit()
    return {
        "ok": True,
        "copied_from": source_slug,
        "copied_to": dest_slug,
        "knowledge_docs_copied": len(src_docs),
    }


@router.delete("/{slug}")
async def delete_workspace(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
):
    """Admin-only: permanently delete a workspace and all its data."""
    settings = get_settings()
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(403, "Admin key required")
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    await db.delete(workspace)
    await db.commit()
    return {"ok": True, "deleted": slug}


@router.delete("/{slug}/appointments")
async def delete_all_workspace_appointments(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
):
    """Admin-only: delete all booking records for a workspace."""
    settings = get_settings()
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(403, "Admin key required")
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    bookings_result = await db.execute(
        select(Booking).where(Booking.workspace_id == workspace.id)
    )
    bookings = bookings_result.scalars().all()
    for b in bookings:
        await db.delete(b)
    await db.commit()
    return {"ok": True, "deleted": len(bookings)}


@router.delete("/{slug}/appointments/{booking_id}")
async def delete_appointment(
    slug: str,
    booking_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Delete a single booking record. Requires admin key or workspace owner."""
    settings = get_settings()
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    if not is_admin:
        if not user_id:
            raise HTTPException(401, "Authentication required")
        owner_check = await db.execute(
            select(WorkspaceOwner).where(
                WorkspaceOwner.workspace_id == workspace.id,
                WorkspaceOwner.user_id == user_id,
            )
        )
        if not owner_check.scalar_one_or_none():
            raise HTTPException(403, "Access denied")
    booking = await db.get(Booking, booking_id)
    if not booking or booking.workspace_id != workspace.id:
        raise HTTPException(404, "Appointment not found")

    # Best-effort: cancel on the calendar provider before removing from DB
    calendar_cancelled = False
    if booking.event_uri:
        try:
            from app.services.calendar.registry import get_provider_for_workspace
            provider = await get_provider_for_workspace(workspace, db)
            if provider:
                calendar_cancelled = await provider.cancel_booking(booking.event_uri)
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning("Could not cancel booking on provider: %s", e)

    await db.delete(booking)
    await db.commit()
    return {"ok": True, "calendar_cancelled": calendar_cancelled}


@router.get("/{slug}/appointments", response_model=list[AppointmentItem])
async def get_workspace_appointments(
    slug: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    settings = get_settings()
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    if not is_admin:
        if not user_id:
            raise HTTPException(401, "Authentication required")
        owner_check = await db.execute(
            select(WorkspaceOwner).where(
                WorkspaceOwner.workspace_id == workspace.id,
                WorkspaceOwner.user_id == user_id,
            )
        )
        if not owner_check.scalar_one_or_none():
            raise HTTPException(403, "Access denied")

    bookings_result = await db.execute(
        select(Booking)
        .where(
            Booking.workspace_id == workspace.id,
            # Only show confirmed bookings:
            # - event_uri set = confirmed via Calendly webhook
            # - session_id set = booked directly via Google/Outlook
            or_(
                Booking.event_uri.is_not(None),
                Booking.session_id.is_not(None),
            ),
        )
        .order_by(Booking.scheduled_for.desc())
        .limit(limit)
    )
    bookings = bookings_result.scalars().all()

    return [
        AppointmentItem(
            id=b.id,
            customer_name=b.customer_name,
            customer_email=b.customer_email,
            service_name=b.service_name,
            channel=b.channel,
            scheduled_for=b.scheduled_for.isoformat(),
            duration_minutes=b.duration_minutes,
        )
        for b in bookings
    ]
