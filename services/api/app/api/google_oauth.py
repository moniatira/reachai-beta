"""Google Calendar OAuth flow.

GET  /v1/google/connect/{slug}   → redirects to Google consent screen
GET  /v1/google/callback          → handles OAuth callback, saves tokens
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.models import Workspace
from app.models.calendar_connection import CalendarConnection
from app.services.calendar.google_provider import GOOGLE_AUTH_URL, GOOGLE_SCOPES, GOOGLE_TOKEN_URL
from app.core.security import encrypt_token


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/google", tags=["google_oauth"])


def _state_serializer():
    settings = get_settings()
    return URLSafeTimedSerializer(settings.session_secret_key, salt="google-oauth")


@router.get("/connect/{slug}")
async def google_connect(slug: str, db: AsyncSession = Depends(get_db)):
    """Start the Google OAuth flow for a workspace."""
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(503, "Google OAuth not configured on server")

    # Verify workspace exists
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")

    # Build signed state to prevent CSRF + carry workspace context
    serializer = _state_serializer()
    nonce = secrets.token_urlsafe(16)
    state = serializer.dumps({"slug": slug, "nonce": nonce})

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # Force refresh token issuance
        "state": state,
        "include_granted_scopes": "true",
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(auth_url)


@router.get("/callback")
async def google_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback and save tokens."""
    if error:
        return HTMLResponse(_error_page(f"Google authorization failed: {error}"), status_code=400)
    if not code or not state:
        return HTMLResponse(_error_page("Missing authorization code"), status_code=400)

    settings = get_settings()

    # Verify state
    try:
        state_data = _state_serializer().loads(state, max_age=600)
    except BadSignature:
        return HTMLResponse(_error_page("Invalid or expired authorization state"), status_code=400)

    slug = state_data["slug"]

    # Load workspace
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        return HTMLResponse(_error_page("Workspace no longer exists"), status_code=404)

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            token_resp = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        except httpx.HTTPError as e:
            logger.error("Google token exchange network error: %s", e)
            return HTMLResponse(_error_page("Could not connect to Google"), status_code=502)

    if token_resp.status_code != 200:
        logger.error("Google token exchange failed %s: %s", token_resp.status_code, token_resp.text)
        return HTMLResponse(_error_page("Google rejected the authorization"), status_code=400)

    tokens = token_resp.json()
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 3600)

    if not refresh_token:
        return HTMLResponse(
            _error_page(
                "Google didn't return a refresh token. Please revoke access in your "
                "Google Account settings and try connecting again."
            ),
            status_code=400,
        )

    # Fetch account email for display
    account_email = None
    account_id = None
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            user_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code == 200:
                udata = user_resp.json()
                account_email = udata.get("email")
                account_id = udata.get("id")
        except httpx.HTTPError:
            pass  # Non-fatal — token still works

    # Upsert calendar connection
    existing_result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.workspace_id == workspace.id,
            CalendarConnection.provider == "google",
        )
    )
    existing = existing_result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=expires_in)

    if existing:
        existing.access_token_enc = encrypt_token(access_token)
        existing.refresh_token_enc = encrypt_token(refresh_token)
        existing.expires_at = expires_at
        existing.account_email = account_email
        existing.account_id = account_id
        existing.active = True
    else:
        new_conn = CalendarConnection(
            workspace_id=workspace.id,
            provider="google",
            access_token_enc=encrypt_token(access_token),
            refresh_token_enc=encrypt_token(refresh_token),
            expires_at=expires_at,
            account_email=account_email,
            account_id=account_id,
            connection_metadata={"calendar_ids": ["primary"]},
            active=True,
        )
        db.add(new_conn)

    # Make Google the primary calendar (newly connected = active choice)
    workspace.primary_calendar_provider = "google"

    await db.commit()

    return HTMLResponse(_success_page(workspace.name, "Google Calendar", account_email))


def _success_page(workspace_name: str, provider: str, account_email: str | None) -> str:
    email_line = f"<p class='small'>Connected as <b>{account_email}</b></p>" if account_email else ""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Connected · ReachAI</title>
<style>
body{{font-family:-apple-system,sans-serif;background:#FAFAFC;color:#1A1F3D;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #E5E5EE;border-radius:14px;padding:48px;max-width:480px;text-align:center}}
.check{{width:64px;height:64px;border-radius:50%;background:#E1F5EE;color:#0F6E56;display:flex;align-items:center;justify-content:center;font-size:28px;margin:0 auto 22px}}
h1{{font-size:24px;margin:0 0 10px;font-weight:600}}
p{{color:#5F5E5A;line-height:1.6;font-size:15px;margin:0 0 12px}}
.small{{font-size:13px;color:#888780}}
.brand{{margin-top:32px;color:#534AB7;font-weight:600;font-size:14px}}
</style>
</head>
<body>
<div class="card">
  <div class="check">✓</div>
  <h1>{provider} connected!</h1>
  <p>{workspace_name} can now book appointments through {provider}.</p>
  {email_line}
  <p class="small">You can close this window and return to your dashboard.</p>
  <div class="brand">R∙ ReachAI</div>
</div>
</body>
</html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Connection failed · ReachAI</title>
<style>
body{{font-family:-apple-system,sans-serif;background:#FAFAFC;color:#1A1F3D;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #E5E5EE;border-radius:14px;padding:36px;max-width:480px}}
h1{{color:#A32D2D;font-size:20px;margin:0 0 12px;font-weight:600}}
p{{color:#5F5E5A;line-height:1.6;font-size:15px;margin:0 0 18px}}
.brand{{margin-top:24px;color:#534AB7;font-weight:600;font-size:14px}}
</style></head>
<body>
<div class="card">
  <h1>Couldn't connect Google Calendar</h1>
  <p>{message}</p>
  <p class="small">If this keeps happening, contact support.</p>
  <div class="brand">R∙ ReachAI</div>
</div>
</body>
</html>"""
