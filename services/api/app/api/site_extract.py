"""Endpoints for triggering site extraction.

POST /v1/workspaces/{slug}/extract
    Crawls the website (from request body or workspace.website_url),
    extracts structured business info via Claude, saves to workspace.
    Requires admin key OR workspace ownership.

GET /v1/workspaces/{slug}/extracted-info
    Returns the current extracted_business_info for a workspace.
    For debugging / dashboard display.
"""
from __future__ import annotations
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import get_current_user_optional
from app.models import Workspace
from app.models.workspace import WorkspaceOwner
from app.services.site_extractor import ExtractError, crawl_business_site
from app.services.site_summarizer import SummarizerError, extract_business_info


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/workspaces", tags=["site_extraction"])


class ExtractRequest(BaseModel):
    website_url: HttpUrl | None = None


async def _load_workspace_with_auth(
    db: AsyncSession,
    slug: str,
    x_admin_key: str | None,
    user_id: str | None,
) -> Workspace:
    """Load workspace, enforce admin OR owner access."""
    settings = get_settings()
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key

    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")

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


@router.post("/{slug}/extract")
async def extract_site_info(
    slug: str,
    payload: ExtractRequest,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Crawl the SMB's website and extract structured business info."""
    workspace = await _load_workspace_with_auth(db, slug, x_admin_key, user_id)

    # Determine which URL to crawl
    target_url = str(payload.website_url) if payload.website_url else workspace.website_url
    if not target_url:
        raise HTTPException(
            400,
            "No website URL provided. Pass website_url in body, or set workspace.website_url first.",
        )

    # If the request provided a URL, also update the workspace
    if payload.website_url:
        workspace.website_url = str(payload.website_url)

    # 1. Crawl
    logger.info("Starting extraction for %s from %s", slug, target_url)
    try:
        crawl_result = await crawl_business_site(target_url)
    except ExtractError as e:
        raise HTTPException(400, f"Crawl failed: {e}")

    if crawl_result["pages_crawled"] == 0:
        raise HTTPException(400, "No pages could be fetched from the URL")

    # 2. Extract with Claude
    try:
        extracted = await extract_business_info(crawl_result["combined_text"])
    except SummarizerError as e:
        raise HTTPException(500, f"Extraction failed: {e}")

    # 3. Save to workspace
    workspace.extracted_business_info = extracted
    await db.commit()
    await db.refresh(workspace)

    return {
        "ok": True,
        "slug": slug,
        "website_url": target_url,
        "pages_crawled": crawl_result["pages_crawled"],
        "pages": crawl_result["pages"],
        "extracted": extracted,
    }


@router.get("/{slug}/extracted-info")
async def get_extracted_info(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Return the current extracted_business_info for a workspace."""
    workspace = await _load_workspace_with_auth(db, slug, x_admin_key, user_id)
    return {
        "slug": slug,
        "website_url": workspace.website_url,
        "extracted_business_info": workspace.extracted_business_info,
        "has_data": workspace.extracted_business_info is not None,
    }
