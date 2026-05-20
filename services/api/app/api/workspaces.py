"""Workspace management endpoints."""
import re
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import require_admin
from app.models import Workspace
from app.services.calendly import build_authorize_url


router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


class WorkspaceCreate(BaseModel):
    slug: str = Field(..., description="URL-safe identifier (e.g. 'acme-salon')")
    name: str = Field(..., min_length=2, max_length=200)
    owner_email: EmailStr
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


@router.post("", response_model=WorkspaceResponse, dependencies=[Depends(require_admin)])
async def create_workspace(
    payload: WorkspaceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a whitelisted workspace. Admin-only."""
    if not SLUG_RE.match(payload.slug):
        raise HTTPException(
            status_code=400,
            detail="Slug must be 3-80 chars: lowercase letters, digits, hyphens",
        )

    existing = await db.execute(select(Workspace).where(Workspace.slug == payload.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Slug '{payload.slug}' is taken")

    workspace = Workspace(
        slug=payload.slug,
        name=payload.name,
        owner_email=payload.owner_email,
        industry=payload.industry,
        assistant_name=payload.assistant_name,
        tone=payload.tone,
        brand_primary=payload.brand_primary,
        whitelisted=True,
    )
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)

    from app.core.config import get_settings
    settings = get_settings()
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
    )


@router.get("/{slug}", response_model=WorkspaceResponse, dependencies=[Depends(require_admin)])
async def get_workspace(slug: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Workspace).where(Workspace.slug == slug)
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    await db.refresh(workspace, ["calendly_token"])

    from app.core.config import get_settings
    settings = get_settings()
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
    )
