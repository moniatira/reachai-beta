"""Onboarding wizard endpoints — authenticated user creates a workspace
and walks through the 5-step setup flow.

Flow:
1. POST /v1/onboarding/start         → create workspace, become its owner
2. PATCH /v1/onboarding/business     → save step-1 data
3. (User completes Calendly OAuth via existing /v1/calendly/connect/{slug})
4. PATCH /v1/onboarding/assistant    → save step-3 data
5. POST /v1/onboarding/complete      → mark ready_to_trial (Day 5 wires Stripe here)

Each step is idempotent — the wizard can pause and resume.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.jwt_utils import get_current_user_id
from app.models import Workspace
from app.models.workspace import (
    ASSISTANT,
    BUSINESS,
    CALENDAR,
    COMPLETE,
    NOT_STARTED,
    PENDING,
    WorkspaceOwner,
)
from app.models.user import User


router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")


def _slugify(text: str) -> str:
    """Convert any business name into a URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) < 3:
        slug = (slug + "-biz")[:80]
    return slug[:80]


async def _unique_slug(db: AsyncSession, base: str) -> str:
    """Find a unique slug, appending -2, -3, … if collisions exist."""
    candidate = base
    suffix = 2
    while True:
        existing = await db.execute(select(Workspace).where(Workspace.slug == candidate))
        if not existing.scalar_one_or_none():
            return candidate
        candidate = f"{base}-{suffix}"[:80]
        suffix += 1
        if suffix > 100:
            raise HTTPException(500, "Could not allocate unique slug")


# ─── Pydantic schemas ─────────────────────────────────────────────────────────


class OnboardingStartRequest(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=200)


class OnboardingBusinessRequest(BaseModel):
    workspace_id: str
    business_name: str | None = Field(None, min_length=2, max_length=200)
    slug: str | None = None
    industry: str | None = Field(None, max_length=80)
    website_url: HttpUrl | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not SLUG_RE.match(v):
            raise ValueError("Slug must be 3-80 chars: lowercase letters, digits, hyphens; cannot start with hyphen")
        return v


class OnboardingAssistantRequest(BaseModel):
    workspace_id: str
    assistant_name: str | None = Field(None, max_length=80)
    greeting: str | None = Field(None, max_length=500)
    tone: str | None = Field(None, max_length=40)
    brand_primary: str | None = Field(None, max_length=20)


class OnboardingCompleteRequest(BaseModel):
    workspace_id: str


class WorkspaceSummary(BaseModel):
    id: str
    slug: str
    name: str
    onboarding_step: str
    trial_status: str
    calendly_connected: bool


class OnboardingStatus(BaseModel):
    workspaces: list[WorkspaceSummary]


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _load_owned_workspace(
    db: AsyncSession, workspace_id: str, user_id: str
) -> Workspace:
    """Load a workspace if it exists AND the user owns it. 404/403 otherwise."""
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    owner_check = await db.execute(
        select(WorkspaceOwner).where(
            WorkspaceOwner.workspace_id == workspace_id,
            WorkspaceOwner.user_id == user_id,
        )
    )
    if not owner_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this workspace",
        )

    return workspace


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/start", response_model=WorkspaceSummary)
async def start_onboarding(
    payload: OnboardingStartRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a new workspace owned by the current user, in pending state."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    base_slug = _slugify(payload.business_name)
    final_slug = await _unique_slug(db, base_slug)

    workspace = Workspace(
        slug=final_slug,
        name=payload.business_name.strip(),
        owner_email=user.email,
        owner_user_id=user.id,
        whitelisted=True,
        active=True,
        onboarding_step=BUSINESS,
        trial_status=PENDING,
    )
    db.add(workspace)
    await db.flush()

    db.add(WorkspaceOwner(user_id=user.id, workspace_id=workspace.id, role="owner"))
    await db.commit()
    await db.refresh(workspace)

    return WorkspaceSummary(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        onboarding_step=workspace.onboarding_step,
        trial_status=workspace.trial_status,
        calendly_connected=False,
    )


@router.patch("/business", response_model=WorkspaceSummary)
async def update_business(
    payload: OnboardingBusinessRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Save step-1 business info. Allows slug change if not yet taken."""
    workspace = await _load_owned_workspace(db, payload.workspace_id, user_id)

    if payload.business_name is not None:
        workspace.name = payload.business_name.strip()

    if payload.slug is not None and payload.slug != workspace.slug:
        existing = await db.execute(
            select(Workspace).where(
                Workspace.slug == payload.slug,
                Workspace.id != workspace.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"Slug '{payload.slug}' is taken")
        workspace.slug = payload.slug

    if payload.industry is not None:
        workspace.industry = payload.industry

    if payload.website_url is not None:
        workspace.website_url = str(payload.website_url)

    if workspace.onboarding_step == NOT_STARTED:
        workspace.onboarding_step = BUSINESS

    await db.commit()
    await db.refresh(workspace, ["calendly_token"])

    return WorkspaceSummary(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        onboarding_step=workspace.onboarding_step,
        trial_status=workspace.trial_status,
        calendly_connected=workspace.calendly_token is not None,
    )


@router.patch("/assistant", response_model=WorkspaceSummary)
async def update_assistant(
    payload: OnboardingAssistantRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Save step-3 assistant settings."""
    workspace = await _load_owned_workspace(db, payload.workspace_id, user_id)

    if payload.assistant_name is not None:
        workspace.assistant_name = payload.assistant_name.strip() or "Sarah"
    if payload.greeting is not None:
        workspace.greeting = payload.greeting.strip()
    if payload.tone is not None:
        workspace.tone = payload.tone
    if payload.brand_primary is not None:
        workspace.brand_primary = payload.brand_primary

    # Advance step (only if calendar connected — otherwise stay at calendar step)
    await db.refresh(workspace, ["calendly_token"])
    if workspace.calendly_token and workspace.onboarding_step in (BUSINESS, CALENDAR):
        workspace.onboarding_step = ASSISTANT

    await db.commit()
    await db.refresh(workspace, ["calendly_token"])

    return WorkspaceSummary(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        onboarding_step=workspace.onboarding_step,
        trial_status=workspace.trial_status,
        calendly_connected=workspace.calendly_token is not None,
    )


@router.post("/complete", response_model=WorkspaceSummary)
async def complete_onboarding(
    payload: OnboardingCompleteRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Mark onboarding complete. Day 5 will add Stripe checkout here."""
    workspace = await _load_owned_workspace(db, payload.workspace_id, user_id)
    await db.refresh(workspace, ["calendly_token"])

    if not workspace.calendly_token:
        raise HTTPException(
            status_code=400,
            detail="Connect a calendar before completing onboarding.",
        )

    workspace.onboarding_step = COMPLETE
    # Day 5 will set trial_status=TRIAL and trial_ends_at after Stripe checkout.
    # For Day 2, we just mark it ready.
    await db.commit()
    await db.refresh(workspace)

    return WorkspaceSummary(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        onboarding_step=workspace.onboarding_step,
        trial_status=workspace.trial_status,
        calendly_connected=True,
    )


@router.get("/status", response_model=OnboardingStatus)
async def onboarding_status(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return all workspaces this user owns and their wizard progress."""
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceOwner, WorkspaceOwner.workspace_id == Workspace.id)
        .where(WorkspaceOwner.user_id == user_id)
        .order_by(Workspace.created_at.desc())
    )
    workspaces = result.scalars().all()

    summaries = []
    for w in workspaces:
        await db.refresh(w, ["calendly_token"])
        summaries.append(
            WorkspaceSummary(
                id=w.id,
                slug=w.slug,
                name=w.name,
                onboarding_step=w.onboarding_step,
                trial_status=w.trial_status,
                calendly_connected=w.calendly_token is not None,
            )
        )

    return OnboardingStatus(workspaces=summaries)
