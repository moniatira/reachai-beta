"""Outlook/Microsoft Graph calendar provider — find slots, create events with Teams links.

Uses Microsoft Graph API v1.0. OAuth 2.0 via MSAL.
Token refresh handled inline; tokens stored encrypted in calendar_connections.

Required Graph permissions: Calendars.ReadWrite, User.Read, offline_access
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models.calendar_connection import CalendarConnection
from app.services.calendar.base import (
    BookingConfirmation,
    BookingRequest,
    CalendarProvider,
    CalendarProviderError,
    CalendarService,
    CalendarSlot,
    TokenExpiredError,
    generate_time_grid,
    slots_overlap,
)
from app.services.encryption import decrypt_token, encrypt_token


logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
OUTLOOK_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
OUTLOOK_SCOPES = "Calendars.ReadWrite User.Read offline_access"


class OutlookCalendarProvider(CalendarProvider):
    PROVIDER_NAME = "outlook"

    def __init__(
        self,
        connection: CalendarConnection,
        client_id: str,
        client_secret: str,
        tenant_id: str = "common",
    ):
        self.connection = connection
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id

    @property
    def supports_video_conferencing(self) -> bool:
        return True

    @property
    def supports_real_time_booking(self) -> bool:
        return True

    # ─── Token management ─────────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """Get a fresh access token, refreshing if needed."""
        now = datetime.now(timezone.utc)
        needs_refresh = (
            self.connection.expires_at is None
            or self.connection.expires_at <= now + timedelta(minutes=5)
        )

        if not needs_refresh:
            return decrypt_token(self.connection.access_token_enc)

        # Refresh token flow
        refresh_token = decrypt_token(self.connection.refresh_token_enc)
        token_url = OUTLOOK_TOKEN_URL_TEMPLATE.format(tenant=self.tenant_id)

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    token_url,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": OUTLOOK_SCOPES,
                    },
                )
            except httpx.HTTPError as e:
                raise TokenExpiredError(f"Outlook refresh failed: {e}")

        if resp.status_code != 200:
            logger.error("Outlook refresh failed %s: %s", resp.status_code, resp.text)
            raise TokenExpiredError("Outlook connection expired. Reconnect required.")

        data = resp.json()
        new_access = data["access_token"]
        new_refresh = data.get("refresh_token", refresh_token)
        expires_in = data.get("expires_in", 3600)

        self.connection.access_token_enc = encrypt_token(new_access)
        self.connection.refresh_token_enc = encrypt_token(new_refresh)
        self.connection.expires_at = now + timedelta(seconds=expires_in)

        return new_access

    async def _graph_request(
        self, method: str, path: str, json: dict | None = None, params: dict | None = None
    ) -> dict:
        """Make an authenticated Graph API request."""
        access_token = await self._get_access_token()
        url = f"{GRAPH_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.request(method, url, headers=headers, json=json, params=params)
            except httpx.HTTPError as e:
                raise CalendarProviderError(f"Outlook Graph network error: {e}")

        if resp.status_code == 401:
            raise TokenExpiredError("Outlook token rejected; reconnect required")
        if resp.status_code >= 400:
            logger.error("Outlook Graph error %s: %s", resp.status_code, resp.text[:500])
            raise CalendarProviderError(f"Outlook Graph {resp.status_code}: {resp.text[:300]}")

        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ─── Services ──────────────────────────────────────────────────────────────

    async def list_services(self) -> list[CalendarService]:
        """Outlook has no built-in services concept — return SMB-configured
        services or a default 30-min consultation."""
        configured = self.connection.connection_metadata.get("services") if self.connection.connection_metadata else None
        if configured:
            return [CalendarService(**s) for s in configured]

        return [
            CalendarService(
                id="default-30min",
                name="30-minute consultation",
                duration_minutes=30,
                description="Standard appointment",
                location="video",
            )
        ]

    # ─── Slot finding ──────────────────────────────────────────────────────────

    async def find_available_slots(
        self,
        service_id: str,
        date_range_start: datetime,
        date_range_end: datetime,
        max_slots: int = 10,
    ) -> list[CalendarSlot]:
        """Use Graph getSchedule (free/busy) to find available windows."""
        services = await self.list_services()
        service = next((s for s in services if s.id == service_id), services[0])

        user_email = self.connection.account_email or "me"

        # Graph getSchedule call
        body = {
            "schedules": [user_email],
            "startTime": {
                "dateTime": date_range_start.isoformat(),
                "timeZone": "UTC",
            },
            "endTime": {
                "dateTime": date_range_end.isoformat(),
                "timeZone": "UTC",
            },
            "availabilityViewInterval": service.duration_minutes,
        }

        try:
            response = await self._graph_request(
                "POST", "/me/calendar/getSchedule", json=body
            )
        except CalendarProviderError as e:
            logger.error("Outlook getSchedule failed: %s", e)
            raise

        # Parse busy windows from the response
        busy_windows: list[tuple[datetime, datetime]] = []
        for schedule in response.get("value", []):
            for sched_item in schedule.get("scheduleItems", []):
                status = sched_item.get("status", "free")
                if status in ("busy", "oof", "workingElsewhere", "tentative"):
                    s = sched_item["start"]
                    e = sched_item["end"]
                    bs = datetime.fromisoformat(s["dateTime"]).replace(tzinfo=timezone.utc)
                    be = datetime.fromisoformat(e["dateTime"]).replace(tzinfo=timezone.utc)
                    busy_windows.append((bs, be))

        candidates = generate_time_grid(
            date_range_start,
            date_range_end,
            slot_minutes=service.duration_minutes,
        )

        available = []
        for slot_start, slot_end in candidates:
            if any(slots_overlap(slot_start, slot_end, bs, be) for bs, be in busy_windows):
                continue
            available.append(CalendarSlot(
                start=slot_start,
                end=slot_end,
                service_id=service.id,
            ))
            if len(available) >= max_slots:
                break

        return available

    # ─── Booking ───────────────────────────────────────────────────────────────

    async def create_booking(self, request: BookingRequest) -> BookingConfirmation:
        """Create event in Outlook calendar. Optionally include Teams link."""
        services = await self.list_services()
        service = next((s for s in services if s.id == request.service_id), services[0])

        event_body: dict[str, Any] = {
            "subject": f"{service.name} — {request.customer_name}",
            "body": {
                "contentType": "HTML",
                "content": (
                    f"Booked via ReachAI<br><br>"
                    f"<b>Customer:</b> {request.customer_name}<br>"
                    f"<b>Email:</b> {request.customer_email}<br>"
                    + (f"<b>Phone:</b> {request.customer_phone}<br>" if request.customer_phone else "")
                    + (f"<br><b>Notes:</b><br>{request.notes}" if request.notes else "")
                ),
            },
            "start": {
                "dateTime": request.slot.start.isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": request.slot.end.isoformat(),
                "timeZone": "UTC",
            },
            "attendees": [{
                "emailAddress": {
                    "address": request.customer_email,
                    "name": request.customer_name,
                },
                "type": "required",
            }],
        }

        if request.add_video_conferencing:
            event_body["isOnlineMeeting"] = True
            event_body["onlineMeetingProvider"] = "teamsForBusiness"

        try:
            created = await self._graph_request("POST", "/me/events", json=event_body)
        except CalendarProviderError as e:
            logger.error("Outlook event creation failed: %s", e)
            raise

        join_url = None
        if request.add_video_conferencing:
            online_meeting = created.get("onlineMeeting", {}) or {}
            join_url = online_meeting.get("joinUrl")

        return BookingConfirmation(
            booking_id=created["id"],
            confirmation_url=created.get("webLink"),
            join_url=join_url,
            confirmed_at=datetime.now(timezone.utc),
            requires_customer_confirmation=False,
        )

    async def health_check(self) -> bool:
        """Quick API call to verify connection."""
        try:
            await self._graph_request("GET", "/me", params={"$select": "id,mail"})
            return True
        except Exception as e:
            logger.warning("Outlook health check failed: %s", e)
            return False
