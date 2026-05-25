"""Magic link authentication endpoints.

Flow:
1. POST /v1/auth/request-link  → email sent with magic link
2. User clicks link → GET /v1/auth/verify-link?token=...
3. Backend exchanges magic token for session JWT
4. Frontend stores session JWT, calls authed endpoints
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import (
    MAGIC_LINK_EXPIRY_MINUTES,
    create_magic_token,
    create_session_token,
    decode_token,
    get_current_user_id,
)
from app.models.user import MagicLink, User
from app.services.email_templates import magic_link_email
from app.services.resend_client import EmailError, send_email


logger = logging.getLogger(__name__)
settings = get_settings()


router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _hash_token(token: str) -> str:
    """Hash a magic token before storing in DB (defense-in-depth)."""
    return hashlib.sha256(token.encode()).hexdigest()


class RequestLinkPayload(BaseModel):
    email: EmailStr


class RequestLinkResponse(BaseModel):
    ok: bool
    message: str


@router.post("/request-link", response_model=RequestLinkResponse)
async def request_magic_link(
    payload: RequestLinkPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a magic link to the requested email.

    Always returns success, even if the email isn't in our system —
    this prevents user enumeration attacks. The email won't actually
    arrive if Resend rejects the address, but the caller can't tell.
    """
    email = payload.email.lower().strip()

    # Find or create user. We create users on first request — this is the
    # "instant sign-up" pattern. They become real after verifying.
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    is_new_user = user is None

    if is_new_user:
        user = User(email=email, is_active=True)
        db.add(user)
        await db.flush()

    # Generate token
    magic_token = create_magic_token(email)

    # Store hashed token + audit info
    magic_link_record = MagicLink(
        email=email,
        token_hash=_hash_token(magic_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500],
    )
    db.add(magic_link_record)
    await db.commit()

    # Build click URL
    magic_url = f"{settings.magic_link_base_url}?token={magic_token}"

    # Send email
    subject, html, text = magic_link_email(magic_url, is_new_user=is_new_user)

    try:
        await send_email(to=email, subject=subject, html=html, text=text)
    except EmailError as e:
        logger.error("Failed to send magic link to %s: %s", email, e)
        # Don't reveal the failure to the caller — same response regardless
        # In dev mode, log the URL so testing is possible
        if settings.environment == "development":
            logger.info("DEV ONLY — magic link: %s", magic_url)

    return RequestLinkResponse(
        ok=True,
        message="If that email is valid, a sign-in link has been sent. Check your inbox.",
    )


@router.get("/verify-link", response_class=HTMLResponse)
async def verify_magic_link(
    token: str = Query(..., description="Magic link token from email"),
    db: AsyncSession = Depends(get_db),
):
    """Click target for the magic link email.

    On success, returns an HTML page that:
    1. Stores the session JWT in localStorage
    2. Redirects to the app dashboard or onboarding wizard
    """
    # Decode and validate magic token
    try:
        payload = decode_token(token, expected_type="magic")
    except HTTPException as e:
        return HTMLResponse(_error_page(e.detail), status_code=e.status_code)

    email = payload["email"]
    token_hash = _hash_token(token)

    # Look up the magic link record
    result = await db.execute(
        select(MagicLink).where(MagicLink.token_hash == token_hash)
    )
    magic_record = result.scalar_one_or_none()

    if not magic_record:
        return HTMLResponse(_error_page("This link is invalid or has been tampered with."), status_code=400)

    if magic_record.used:
        return HTMLResponse(
            _error_page("This link has already been used. Request a new one to sign in."),
            status_code=400,
        )

    # Mark used (single-use enforcement)
    magic_record.used = True
    magic_record.used_at = datetime.now(timezone.utc)

    # Find the user (created during request-link step)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        # Shouldn't happen — but handle gracefully
        user = User(email=email, is_active=True)
        db.add(user)
        await db.flush()

    if not user.is_active:
        return HTMLResponse(_error_page("This account is deactivated."), status_code=403)

    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    # Issue session JWT
    session_jwt = create_session_token(user_id=user.id, email=user.email)

    return HTMLResponse(_success_page(session_jwt))


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str | None = None
    created_at: datetime
    last_login_at: datetime | None = None


@router.get("/me", response_model=MeResponse)
async def get_me(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Return current user info. Requires Authorization: Bearer <session_jwt>."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.post("/logout")
async def logout():
    """No-op endpoint for client convention. JWTs are stateless — client
    discards the token to log out. This endpoint exists so frontend code
    can `POST /v1/auth/logout` symmetrically with `POST /v1/auth/request-link`.
    """
    return {"ok": True, "message": "Logged out. Discard your session token client-side."}


# ── HTML helpers ──────────────────────────────────────────────────────────────


def _success_page(session_token: str) -> str:
    """Renders a page that captures the session token and redirects to the app."""
    redirect = settings.app_base_url
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Signed in · ReachAI</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#FAFAFC;color:#1A1F3D;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #E5E5EE;border-radius:14px;padding:48px;max-width:440px;text-align:center;box-shadow:0 20px 48px rgba(83,74,183,.08)}}
.check{{width:64px;height:64px;border-radius:50%;background:#E1F5EE;color:#0F6E56;display:flex;align-items:center;justify-content:center;font-size:28px;margin:0 auto 22px}}
h1{{font-size:26px;margin:0 0 10px;font-weight:600}}
p{{color:#5F5E5A;font-size:15px;line-height:1.6;margin:0 0 18px}}
.brand{{margin-top:32px;color:#534AB7;font-weight:600;font-size:14px;letter-spacing:.02em}}
.btn{{display:inline-block;background:#534AB7;color:#fff;text-decoration:none;padding:12px 24px;border-radius:8px;font-size:14px;font-weight:500;margin-top:8px}}
</style>
</head>
<body>
<div class="card">
  <div class="check">✓</div>
  <h1>You're signed in.</h1>
  <p>Redirecting you to ReachAI…</p>
  <a class="btn" href="{redirect}" id="continue">Continue →</a>
  <div class="brand">R∙ ReachAI</div>
</div>
<script>
try {{
  localStorage.setItem('reachai_session', '{session_token}');
}} catch (e) {{}}
setTimeout(function() {{
  window.location.href = '{redirect}#session={session_token}';
}}, 800);
</script>
</body>
</html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Sign-in error · ReachAI</title>
<style>
body{{font-family:-apple-system,sans-serif;background:#FAFAFC;color:#1A1F3D;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #E5E5EE;border-radius:14px;padding:36px;max-width:480px}}
h1{{color:#A32D2D;font-size:20px;margin:0 0 12px;font-weight:600}}
p{{color:#5F5E5A;line-height:1.6;font-size:15px;margin:0 0 18px}}
.brand{{margin-top:24px;color:#534AB7;font-weight:600;font-size:14px}}
.btn{{display:inline-block;background:#534AB7;color:#fff;text-decoration:none;padding:11px 22px;border-radius:8px;font-size:14px;font-weight:500}}
</style>
</head>
<body>
<div class="card">
  <h1>Couldn't sign you in</h1>
  <p>{message}</p>
  <a class="btn" href="{settings.app_base_url}">← Back to ReachAI</a>
  <div class="brand">R∙ ReachAI</div>
</div>
</body>
</html>"""
