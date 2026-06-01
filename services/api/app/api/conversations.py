"""Conversations endpoint — list chat sessions for a workspace.

GET /v1/workspaces/{slug}/conversations?limit=30
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
from app.models.workspace import ChatSession, WorkspaceOwner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/workspaces", tags=["conversations"])


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


class ConversationItem(BaseModel):
    id: str
    channel: str
    customer_name: str | None
    customer_email: str | None
    booked: bool
    message_count: int
    created_at: str
    updated_at: str


@router.get("/{slug}/conversations", response_model=list[ConversationItem])
async def list_conversations(
    slug: str,
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """List chat sessions for a workspace, most recent first."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.workspace_id == workspace.id)
        .order_by(ChatSession.created_at.desc())
        .limit(limit)
    )
    sessions = result.scalars().all()

    return [
        ConversationItem(
            id=s.id,
            channel=s.channel,
            customer_name=s.customer_name,
            customer_email=s.customer_email,
            booked=s.booked,
            message_count=len(s.messages) if s.messages else 0,
            created_at=s.created_at.isoformat(),
            updated_at=s.updated_at.isoformat(),
        )
        for s in sessions
    ]
