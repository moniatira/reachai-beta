"""Calendly OAuth flow + API client.

Calendly API docs: https://developers.calendly.com/api-docs
OAuth flow: https://developers.calendly.com/api-docs/c4nrgenq2gioh-getting-access-tokens-using-oauth-2-0
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_token, encrypt_token
from app.models import CalendlyToken, Workspace


settings = get_settings()


class CalendlyError(Exception):
    pass


def build_authorize_url(workspace_slug: str) -> str:
    """URL the SMB clicks to authorize ReachAI to use their Calendly."""
    params = {
        "client_id": settings.calendly_client_id,
        "response_type": "code",
        "redirect_uri": settings.calendly_redirect_uri,
        "state": workspace_slug,
    }
    return f"{settings.calendly_oauth_base}/oauth/authorize?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """Exchange authorization code for access + refresh tokens."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.calendly_oauth_base}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.calendly_client_id,
                "client_secret": settings.calendly_client_secret,
                "code": code,
                "redirect_uri": settings.calendly_redirect_uri,
            },
        )
        if resp.status_code != 200:
            raise CalendlyError(f"Token exchange failed: {resp.status_code} {resp.text}")
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Get a fresh access token when the current one expires."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.calendly_oauth_base}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.calendly_client_id,
                "client_secret": settings.calendly_client_secret,
                "refresh_token": refresh_token,
            },
        )
        if resp.status_code != 200:
            raise CalendlyError(f"Token refresh failed: {resp.status_code} {resp.text}")
        return resp.json()


async def save_tokens(
    db: AsyncSession,
    workspace: Workspace,
    token_response: dict[str, Any],
) -> CalendlyToken:
    """Persist (or update) encrypted Calendly tokens for a workspace."""
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=token_response.get("expires_in", 3600) - 60
    )

    access_token = token_response["access_token"]
    user_info = await get_current_user(access_token)

    existing = workspace.calendly_token
    if existing:
        existing.access_token_enc = encrypt_token(access_token)
        existing.refresh_token_enc = encrypt_token(token_response["refresh_token"])
        existing.expires_at = expires_at
        existing.calendly_user_uri = user_info["uri"]
        existing.calendly_email = user_info.get("email")
        existing.scheduling_url = user_info.get("scheduling_url")
        token = existing
    else:
        token = CalendlyToken(
            workspace_id=workspace.id,
            access_token_enc=encrypt_token(access_token),
            refresh_token_enc=encrypt_token(token_response["refresh_token"]),
            expires_at=expires_at,
            calendly_user_uri=user_info["uri"],
            calendly_email=user_info.get("email"),
            scheduling_url=user_info.get("scheduling_url"),
        )
        db.add(token)

    await db.flush()
    return token


async def get_valid_access_token(db: AsyncSession, workspace: Workspace) -> str:
    """Return a valid Calendly access token, refreshing if necessary."""
    if not workspace.calendly_token:
        raise CalendlyError("Workspace has not connected Calendly")

    token = workspace.calendly_token
    now = datetime.now(timezone.utc)

    if token.expires_at <= now:
        refresh_token = decrypt_token(token.refresh_token_enc)
        refreshed = await refresh_access_token(refresh_token)
        token.access_token_enc = encrypt_token(refreshed["access_token"])
        token.refresh_token_enc = encrypt_token(refreshed["refresh_token"])
        token.expires_at = now + timedelta(seconds=refreshed.get("expires_in", 3600) - 60)
        await db.commit()
        await db.refresh(token)

    return decrypt_token(token.access_token_enc)


async def _api_get(access_token: str, path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{settings.calendly_api_base}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params or {},
        )
        if resp.status_code >= 400:
            raise CalendlyError(f"Calendly API error: {resp.status_code} {resp.text}")
        return resp.json()


async def get_current_user(access_token: str) -> dict:
    data = await _api_get(access_token, "/users/me")
    return data["resource"]


async def list_event_types(db: AsyncSession, workspace: Workspace) -> list[dict]:
    """Return the SMB's bookable services (Calendly event types)."""
    access_token = await get_valid_access_token(db, workspace)
    user_uri = workspace.calendly_token.calendly_user_uri
    data = await _api_get(
        access_token,
        "/event_types",
        params={"user": user_uri, "active": "true"},
    )
    return [
        {
            "uri": et["uri"],
            "name": et["name"],
            "duration_minutes": et["duration"],
            "scheduling_url": et["scheduling_url"],
            "description": et.get("description_plain") or "",
        }
        for et in data.get("collection", [])
    ]


async def get_available_slots(
    db: AsyncSession,
    workspace: Workspace,
    event_type_uri: str,
    start_date: datetime,
    end_date: datetime,
) -> list[dict]:
    """Get available time slots for a specific event type in a date window."""
    access_token = await get_valid_access_token(db, workspace)
    data = await _api_get(
        access_token,
        "/event_type_available_times",
        params={
            "event_type": event_type_uri,
            "start_time": start_date.isoformat(),
            "end_time": end_date.isoformat(),
        },
    )
    return [
        {
            "start_time": slot["start_time"],
            "scheduling_url": slot["scheduling_url"],
            "status": slot.get("status", "available"),
        }
        for slot in data.get("collection", [])
        if slot.get("status") == "available"
    ]


def get_booking_link_for_customer(slot: dict) -> str:
    """Return the customer-facing booking URL for a given slot.

    Calendly's API for creating bookings on someone's behalf requires their
    "Single-Use Scheduling Links" endpoint or sending the customer to a booking
    URL. For the beta we return the booking URL so the AI can hand it to the
    customer to complete in one click.
    """
    return slot["scheduling_url"]
