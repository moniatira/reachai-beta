"""Knowledge base endpoints — upload PDFs, scrape URLs, manage docs.

GET    /v1/workspaces/{slug}/knowledge           → list docs
POST   /v1/workspaces/{slug}/knowledge/url       → scrape a URL
POST   /v1/workspaces/{slug}/knowledge/upload    → upload file (PDF or text)
DELETE /v1/workspaces/{slug}/knowledge/{doc_id}  → delete a doc
"""
from __future__ import annotations

import io
import logging

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import get_current_user_optional
from app.models import Workspace, KnowledgeDocument
from app.models.workspace import WorkspaceOwner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/workspaces", tags=["knowledge"])

MAX_CHARS = 20_000


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


class DocSummary(BaseModel):
    id: str
    source_type: str
    source_name: str
    char_count: int
    created_at: str


class AddUrlRequest(BaseModel):
    url: str


@router.get("/{slug}/knowledge", response_model=list[DocSummary])
async def list_knowledge(
    slug: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """List all knowledge documents for a workspace."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.workspace_id == workspace.id)
        .order_by(KnowledgeDocument.created_at)
    )
    docs = result.scalars().all()

    return [
        DocSummary(
            id=d.id,
            source_type=d.source_type,
            source_name=d.source_name,
            char_count=d.char_count,
            created_at=d.created_at.isoformat(),
        )
        for d in docs
    ]


@router.post("/{slug}/knowledge/url", response_model=DocSummary, status_code=201)
async def add_url(
    slug: str,
    payload: AddUrlRequest,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Scrape a URL and store the extracted text as a knowledge document."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(payload.url, headers={"User-Agent": "ReachAI/1.0 (+https://reachai.co)"})
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as e:
        raise HTTPException(400, f"Failed to fetch URL: HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(400, f"Failed to fetch URL: {e}")

    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n", strip=True)
    content = text[:MAX_CHARS]

    doc = KnowledgeDocument(
        workspace_id=workspace.id,
        source_type="url",
        source_name=payload.url,
        content=content,
        char_count=len(content),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return DocSummary(
        id=doc.id,
        source_type=doc.source_type,
        source_name=doc.source_name,
        char_count=doc.char_count,
        created_at=doc.created_at.isoformat(),
    )


@router.post("/{slug}/knowledge/upload", response_model=DocSummary, status_code=201)
async def upload_file(
    slug: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Upload a PDF or text file as a knowledge document."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    raw = await file.read()
    filename = file.filename or "upload"

    if filename.lower().endswith(".pdf") or file.content_type == "application/pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            pages_text = []
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    pages_text.append(extracted)
            text = "\n".join(pages_text)
        except Exception as e:
            raise HTTPException(400, f"Could not parse PDF: {e}")
        source_type = "pdf"
    else:
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            raise HTTPException(400, f"Could not decode file: {e}")
        source_type = "text"

    content = text[:MAX_CHARS]

    doc = KnowledgeDocument(
        workspace_id=workspace.id,
        source_type=source_type,
        source_name=filename,
        content=content,
        char_count=len(content),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return DocSummary(
        id=doc.id,
        source_type=doc.source_type,
        source_name=doc.source_name,
        char_count=doc.char_count,
        created_at=doc.created_at.isoformat(),
    )


@router.delete("/{slug}/knowledge/{doc_id}")
async def delete_knowledge_doc(
    slug: str,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id: str | None = Depends(get_current_user_optional),
):
    """Delete a knowledge document."""
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id,
            KnowledgeDocument.workspace_id == workspace.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    await db.delete(doc)
    await db.commit()
    return {"ok": True, "deleted": doc_id}
