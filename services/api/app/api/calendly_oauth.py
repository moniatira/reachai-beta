"""Calendly OAuth flow: SMB authorizes ReachAI to read their calendar."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Workspace
from app.services.calendly import (
    CalendlyError,
    build_authorize_url,
    exchange_code_for_tokens,
    save_tokens,
)


router = APIRouter(prefix="/v1/calendly", tags=["calendly"])


@router.get("/connect/{slug}")
async def connect_redirect(slug: str, db: AsyncSession = Depends(get_db)):
    """Convenience redirect to Calendly's OAuth authorize URL."""
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace or not workspace.whitelisted:
        raise HTTPException(status_code=404, detail="Workspace not found or not whitelisted")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(build_authorize_url(workspace.slug))


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(..., description="The workspace slug we sent in the authorize URL"),
    db: AsyncSession = Depends(get_db),
):
    """Calendly redirects here after the SMB authorizes ReachAI."""
    result = await db.execute(select(Workspace).where(Workspace.slug == state))
    workspace = result.scalar_one_or_none()
    if not workspace or not workspace.whitelisted:
        return HTMLResponse(_error_page("Workspace not found or not whitelisted."), status_code=404)

    await db.refresh(workspace, ["calendly_token"])

    try:
        token_response = await exchange_code_for_tokens(code)
        await save_tokens(db, workspace, token_response)
        await db.commit()
    except CalendlyError as e:
        return HTMLResponse(_error_page(f"Calendly error: {e}"), status_code=500)

    return HTMLResponse(_success_page(workspace.name))


def _success_page(name: str) -> str:
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>Connected · ReachAI</title>
<style>
body{{font-family:-apple-system,sans-serif;background:#FAFAFC;color:#1A1F3D;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #E5E5EE;border-radius:14px;padding:48px;
max-width:480px;text-align:center;box-shadow:0 20px 48px rgba(83,74,183,.08)}}
.check{{width:64px;height:64px;border-radius:50%;background:#E1F5EE;
color:#0F6E56;display:flex;align-items:center;justify-content:center;
font-size:28px;margin:0 auto 22px}}
h1{{font-size:26px;margin:0 0 10px;font-weight:600}}
p{{color:#5F5E5A;font-size:15px;line-height:1.6}}
.brand{{margin-top:32px;color:#534AB7;font-weight:600;font-size:14px;letter-spacing:.02em}}
</style></head><body>
<div class="card">
  <div class="check">✓</div>
  <h1>You're connected, {name}.</h1>
  <p>Your Calendly is now wired up. Check your email — we've sent your embed code and next steps.</p>
  <div class="brand">R∙ ReachAI</div>
</div>
</body></html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Error · ReachAI</title>
<style>body{{font-family:-apple-system,sans-serif;padding:48px;max-width:600px;margin:0 auto}}
h1{{color:#A32D2D}}</style></head><body>
<h1>Couldn't connect Calendly</h1>
<p>{message}</p>
<p><a href="javascript:history.back()">← Try again</a></p>
</body></html>"""
