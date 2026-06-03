"""Calendly webhook handler.

Calendly fires invitee.created when a customer completes their booking.
We save the booking record and send our branded confirmation email with
the Calendly reschedule link.

Webhook subscription is registered automatically when the SMB connects
their Calendly account via OAuth (calendly_oauth.py). The signing key is
stored per-workspace in CalendarConnection.connection_metadata so each
workspace's webhooks are verified independently.
"""
import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.models import Workspace
from app.models.calendar_connection import CalendarConnection
from app.models.workspace import Booking
from app.services.calendar.calendly_provider import CalendlyProvider

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


def _verify_signature(raw_body: bytes, sig_header: str | None, signing_key: str) -> bool:
    """Verify Calendly HMAC-SHA256 webhook signature.

    Header format: t=<unix_ts>,v1=<hex_digest>
    Signed message:  <timestamp>.<raw_body>
    """
    if not sig_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(","))
        timestamp = parts["t"]
        expected_sig = parts["v1"]
        msg = f"{timestamp}.{raw_body.decode('utf-8')}".encode()
        computed = hmac.new(signing_key.encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, expected_sig)
    except Exception:
        return False


async def _fetch_event_resource(access_token: str, event_uri: str) -> dict:
    """Fetch a Calendly scheduled_event resource by its full URI."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            event_uri,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Calendly event fetch failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()["resource"]


@router.post("/calendly")
async def calendly_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw_body = await request.body()

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Resolve the organizer to find the workspace's signing key
    organizer_uri = payload.get("created_by", "")
    conn_result = await db.execute(
        select(CalendarConnection).where(
            CalendarConnection.account_id == organizer_uri,
            CalendarConnection.provider == "calendly",
            CalendarConnection.active.is_(True),
        )
    )
    connection = conn_result.scalar_one_or_none()

    # Prefer the per-connection key stored at OAuth time; fall back to global env var
    signing_key = ""
    if connection:
        signing_key = (connection.connection_metadata or {}).get("webhook_signing_key", "")
    if not signing_key:
        signing_key = settings.calendly_webhook_signing_key

    if signing_key:
        sig_header = request.headers.get("Calendly-Webhook-Signature")
        if not _verify_signature(raw_body, sig_header, signing_key):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    else:
        logger.warning("No signing key available for organizer %s — skipping verification", organizer_uri)

    event_name = payload.get("event")
    if event_name == "invitee.created":
        await _handle_invitee_created(db, payload, connection)
    elif event_name == "invitee.canceled":
        logger.info("Calendly invitee.canceled received — no action taken")
    else:
        logger.info("Unhandled Calendly webhook event: %s", event_name)

    return {"status": "ok"}


async def _handle_invitee_created(
    db: AsyncSession, payload: dict, connection: CalendarConnection | None
) -> None:
    """Save booking record and send confirmation email after Calendly confirms."""
    data = payload.get("payload", {})

    customer_name = data.get("name", "Customer")
    customer_email = data.get("email", "")
    reschedule_url = data.get("reschedule_url")
    event_uri = data.get("event", "")

    if not customer_email or not event_uri:
        logger.warning("Calendly webhook: missing email or event URI — skipping")
        return

    if not connection:
        logger.warning("Calendly webhook: no workspace connection found — skipping")
        return

    workspace = await db.get(Workspace, connection.workspace_id)
    if not workspace:
        logger.warning("Calendly webhook: workspace %s not found", connection.workspace_id)
        return

    # Fetch scheduled event details (start/end time, service name)
    try:
        provider = CalendlyProvider(connection=connection, db=db)
        access_token = await provider._get_access_token()
        event_data = await _fetch_event_resource(access_token, event_uri)
    except Exception as exc:
        logger.error("Calendly webhook: could not fetch event details: %s", exc)
        return

    start_str = event_data.get("start_time")
    end_str = event_data.get("end_time")
    service_name = event_data.get("name", "Appointment")
    event_type_uri = event_data.get("event_type", "")

    if not start_str:
        logger.warning("Calendly webhook: event has no start_time — skipping")
        return

    scheduled_for = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    duration_minutes = 30
    if end_str:
        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        duration_minutes = max(1, int((end_dt - scheduled_for).total_seconds() / 60))

    # Deduplication: skip if this event was already saved by direct booking API
    existing = await db.execute(
        select(Booking).where(
            Booking.workspace_id == workspace.id,
            Booking.event_uri == event_uri,
        )
    )
    if existing.scalar_one_or_none():
        logger.info("Calendly webhook: event %s already saved — skipping", event_uri)
        return

    # Save booking record — event_uri marks this as webhook-confirmed
    booking = Booking(
        workspace_id=workspace.id,
        session_id=None,
        customer_name=customer_name,
        customer_email=customer_email,
        event_type_uri=event_type_uri,
        event_uri=event_uri,       # full scheduled_event URI = confirmed by Calendly
        service_name=service_name,
        scheduled_for=scheduled_for,
        duration_minutes=duration_minutes,
    )
    db.add(booking)
    await db.flush()

    # Send our branded confirmation email with Calendly reschedule link
    try:
        from app.services.email_templates import booking_confirmation_email
        from app.services.resend_client import send_email

        chat_url = workspace.website_url or None
        subject, html, text, ics_bytes = booking_confirmation_email(
            customer_name=customer_name,
            service_name=service_name,
            business_name=workspace.name,
            business_email=workspace.owner_email,
            scheduled_for=scheduled_for,
            duration_minutes=duration_minutes,
            reschedule_url=reschedule_url,
            chat_url=chat_url,
        )
        await send_email(
            to=customer_email,
            subject=subject,
            html=html,
            text=text,
            reply_to=workspace.owner_email,
            attachments=[{
                "filename": "appointment.ics",
                "content": base64.b64encode(ics_bytes).decode("ascii"),
            }],
        )
        logger.info("Sent Calendly confirmation email to %s for %s", customer_email, workspace.name)
    except Exception as exc:
        logger.error("Calendly webhook: failed to send confirmation email: %s", exc)

    await db.commit()
    logger.info(
        "Calendly booking saved: workspace=%s customer=%s scheduled=%s",
        workspace.slug, customer_email, scheduled_for.isoformat(),
    )
