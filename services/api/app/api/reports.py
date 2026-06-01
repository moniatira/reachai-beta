"""Reports endpoints — CSV downloads for appointments and conversations.

GET /v1/workspaces/{slug}/reports/appointments   → CSV of all bookings
GET /v1/workspaces/{slug}/reports/conversations  → CSV of all chat sessions

Both endpoints accept `token` as a query param as an alternative to the
Authorization: Bearer header (needed for browser download links).
"""
from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import get_current_user_optional, decode_token, AuthError
from app.models import Workspace
from app.models.workspace import Booking, ChatSession, WorkspaceOwner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/workspaces", tags=["reports"])


async def _resolve_user(
    token_param: str | None,
    user_id_from_header: str | None,
) -> str | None:
    """Return user_id from Bearer header or fallback ?token= query param."""
    if user_id_from_header:
        return user_id_from_header
    if token_param:
        try:
            payload = decode_token(token_param, expected_type="session")
            return payload.get("sub")
        except (AuthError, Exception):
            return None
    return None


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


@router.get("/{slug}/reports/appointments")
async def report_appointments(
    slug: str,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id_header: str | None = Depends(get_current_user_optional),
):
    """Download all bookings as a CSV file."""
    user_id = await _resolve_user(token, user_id_header)
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(Booking)
        .where(Booking.workspace_id == workspace.id)
        .order_by(Booking.scheduled_for.desc())
    )
    bookings = result.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "customer_name", "customer_email", "service_name", "channel",
        "scheduled_for", "duration_minutes", "created_at",
    ])
    for b in bookings:
        writer.writerow([
            b.customer_name,
            b.customer_email,
            b.service_name,
            b.channel,
            b.scheduled_for.isoformat(),
            b.duration_minutes,
            b.created_at.isoformat(),
        ])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={slug}-appointments.csv"},
    )


@router.get("/{slug}/reports/conversations")
async def report_conversations(
    slug: str,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id_header: str | None = Depends(get_current_user_optional),
):
    """Download all chat sessions as a CSV file."""
    user_id = await _resolve_user(token, user_id_header)
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.workspace_id == workspace.id)
        .order_by(ChatSession.created_at.desc())
    )
    sessions = result.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "channel", "customer_name", "customer_email",
        "booked", "message_count", "created_at",
    ])
    for s in sessions:
        writer.writerow([
            s.id,
            s.channel,
            s.customer_name or "",
            s.customer_email or "",
            s.booked,
            len(s.messages) if s.messages else 0,
            s.created_at.isoformat(),
        ])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={slug}-conversations.csv"},
    )
