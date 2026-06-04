"""Calendly OAuth flow: SMB authorizes ReachAI to read their calendar."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Workspace
from app.models.calendar_connection import CalendarConnection
from app.core.config import get_settings
from app.services.calendly import (
    CalendlyError,
    build_authorize_url,
    exchange_code_for_tokens,
    get_current_user,
    register_webhook_subscription,
    save_tokens,
)


router = APIRouter(prefix="/v1/calendly", tags=["calendly"])


@router.get("/connect/{slug}")
async def connect_redirect(slug: str, db: AsyncSession = Depends(get_db)):
    """Redirect to Calendly's OAuth authorize URL."""
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(build_authorize_url(workspace.slug))


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(..., description="The workspace slug sent in the authorize URL"),
    db: AsyncSession = Depends(get_db),
):
    """Calendly redirects here after the SMB authorizes ReachAI."""
    result = await db.execute(select(Workspace).where(Workspace.slug == state))
    workspace = result.scalar_one_or_none()
    if not workspace:
        return HTMLResponse(_error_page("Workspace not found."), status_code=404)

    await db.refresh(workspace, ["calendly_token"])

    try:
        token_response = await exchange_code_for_tokens(code)
        access_token = token_response["access_token"]
        calendly_token = await save_tokens(db, workspace, token_response)

        # Fetch user info to get organization URI (needed for webhook registration)
        user_info = await get_current_user(access_token)
        org_uri = user_info.get("current_organization", "")

        # Build webhook URL from our API base
        settings = get_settings()
        api_base = settings.calendly_redirect_uri.rsplit("/v1/", 1)[0]
        webhook_url = f"{api_base}/v1/webhooks/calendly"

        # Register webhook subscription and capture signing key
        signing_key = await register_webhook_subscription(
            access_token=access_token,
            user_uri=calendly_token.calendly_user_uri,
            organization_uri=org_uri,
            webhook_url=webhook_url,
        )

        conn_meta = {"scheduling_url": calendly_token.scheduling_url}
        if signing_key:
            conn_meta["webhook_signing_key"] = signing_key

        # Upsert: match on account_id (Calendly user URI) so the same account
        # reconnecting updates in place; a different Calendly account creates
        # a new connection for multi-staff support.
        conn_result = await db.execute(
            select(CalendarConnection).where(
                CalendarConnection.workspace_id == workspace.id,
                CalendarConnection.provider == "calendly",
                CalendarConnection.account_id == calendly_token.calendly_user_uri,
            )
        )
        existing_conn = conn_result.scalar_one_or_none()

        if existing_conn:
            existing_conn.access_token_enc = calendly_token.access_token_enc
            existing_conn.refresh_token_enc = calendly_token.refresh_token_enc
            existing_conn.expires_at = calendly_token.expires_at
            existing_conn.account_email = calendly_token.calendly_email
            existing_conn.account_id = calendly_token.calendly_user_uri
            existing_conn.connection_metadata = {
                **(existing_conn.connection_metadata or {}),
                **conn_meta,
            }
            existing_conn.active = True
        else:
            db.add(CalendarConnection(
                workspace_id=workspace.id,
                provider="calendly",
                access_token_enc=calendly_token.access_token_enc,
                refresh_token_enc=calendly_token.refresh_token_enc,
                expires_at=calendly_token.expires_at,
                account_email=calendly_token.calendly_email,
                account_id=calendly_token.calendly_user_uri,
                connection_metadata=conn_meta,
                # Calendly: use email as staff name until the owner sets a custom one
                staff_name=calendly_token.calendly_email,
                active=True,
            ))

        # Set primary provider (first connection wins; can be changed from dashboard)
        if not workspace.primary_calendar_provider:
            workspace.primary_calendar_provider = "calendly"

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
  <h1>Calendly connected!</h1>
  <p>{name} is now linked to your ReachAI workspace. This window will close automatically.</p>
  <div class="brand">R∙ ReachAI</div>
</div>
<script>setTimeout(function(){{window.close();}},1500);</script>
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
