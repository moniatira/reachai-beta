"""Google Calendar provider — find slots and create events with auto Meet links.

Uses Google Calendar API v3 via google-api-python-client.
Tokens stored in calendar_connections table; refreshed automatically.

OAuth scope required: https://www.googleapis.com/auth/calendar
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
from app.core.security import decrypt_token, encrypt_token


logger = logging.getLogger(__name__)


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


class GoogleCalendarProvider(CalendarProvider):
    PROVIDER_NAME = "google"

    def __init__(self, connection: CalendarConnection, client_id: str, client_secret: str):
        self.connection = connection
        self.client_id = client_id
        self.client_secret = client_secret
        self._service = None
        self._creds: Credentials | None = None

    @property
    def supports_video_conferencing(self) -> bool:
        return True

    @property
    def supports_real_time_booking(self) -> bool:
        return True

    # ─── Auth helpers ─────────────────────────────────────────────────────────

    def _build_credentials(self) -> Credentials:
        """Construct Credentials from stored tokens. Refreshes if expired."""
        if self._creds is not None:
            return self._creds

        access_token = decrypt_token(self.connection.access_token_enc)
        refresh_token = decrypt_token(self.connection.refresh_token_enc)

        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URL,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=GOOGLE_SCOPES,
        )

        # Refresh if token is expired or expires within 5 min
        now = datetime.now(timezone.utc)
        needs_refresh = (
            self.connection.expires_at is None
            or self.connection.expires_at <= now + timedelta(minutes=5)
        )
        if needs_refresh:
            try:
                creds.refresh(GoogleAuthRequest())
                # Persist refreshed access token + new expiry
                self.connection.access_token_enc = encrypt_token(creds.token)
                self.connection.expires_at = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else now + timedelta(hours=1)
            except Exception as e:
                logger.error("Google token refresh failed for %s: %s", self.connection.workspace_id, e)
                raise TokenExpiredError("Google connection expired. Reconnect required.")

        self._creds = creds
        return creds

    def _calendar_service(self):
        """Get the Google Calendar API service client (cached)."""
        if self._service is None:
            creds = self._build_credentials()
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # ─── Services ──────────────────────────────────────────────────────────────

    async def list_services(self) -> list[CalendarService]:
        """Google Calendar doesn't have built-in "services" — return the
        SMB-configured services from workspace metadata, or default ones.

        For Day 3, we use a sensible default: one 30-min consultation.
        Day 6+ adds a UI for SMBs to configure their own services.
        """
        configured = self.connection.connection_metadata.get("services") if self.connection.connection_metadata else None
        if configured:
            return [CalendarService(**s) for s in configured]

        # Default service if nothing configured
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
        """Query Google free/busy, return slots that don't overlap busy times."""
        services = await self.list_services()
        service = next((s for s in services if s.id == service_id), services[0])

        # Get list of calendars to check busy times against
        calendar_ids = self.connection.connection_metadata.get("calendar_ids", ["primary"]) if self.connection.connection_metadata else ["primary"]

        try:
            cal_service = self._calendar_service()
            freebusy_response = cal_service.freebusy().query(
                body={
                    "timeMin": date_range_start.isoformat(),
                    "timeMax": date_range_end.isoformat(),
                    "items": [{"id": cid} for cid in calendar_ids],
                }
            ).execute()
        except HttpError as e:
            logger.error("Google freebusy failed: %s", e)
            raise CalendarProviderError(f"Could not query Google Calendar: {e.reason}")
        except Exception as e:
            raise CalendarProviderError(f"Google free/busy error: {e}")

        # Collect all busy windows
        busy_windows: list[tuple[datetime, datetime]] = []
        for cal_id in calendar_ids:
            cal_busy = freebusy_response.get("calendars", {}).get(cal_id, {}).get("busy", [])
            for b in cal_busy:
                bstart = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
                bend = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
                busy_windows.append((bstart, bend))

        # Generate candidate slots and filter
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
                provider_metadata={"calendar_id": calendar_ids[0]},
            ))
            if len(available) >= max_slots:
                break

        return available

    # ─── Booking ───────────────────────────────────────────────────────────────

    async def create_booking(self, request: BookingRequest) -> BookingConfirmation:
        """Create event in Google Calendar with optional Google Meet link."""
        services = await self.list_services()
        service = next((s for s in services if s.id == request.service_id), services[0])
        calendar_id = request.slot.provider_metadata.get("calendar_id", "primary")

        event_body: dict[str, Any] = {
            "summary": f"{service.name} — {request.customer_name}",
            "description": (
                f"Booked via ReachAI\n\n"
                f"Customer: {request.customer_name}\n"
                f"Email: {request.customer_email}\n"
                + (f"Phone: {request.customer_phone}\n" if request.customer_phone else "")
                + (f"\nNotes:\n{request.notes}" if request.notes else "")
            ),
            "start": {
                "dateTime": request.slot.start.isoformat(),
                "timeZone": "UTC",
            },
            "end": {
                "dateTime": request.slot.end.isoformat(),
                "timeZone": "UTC",
            },
            "attendees": [{"email": request.customer_email, "displayName": request.customer_name}],
        }

        # Add Google Meet link
        if request.add_video_conferencing:
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        try:
            cal_service = self._calendar_service()
            created = cal_service.events().insert(
                calendarId=calendar_id,
                body=event_body,
                conferenceDataVersion=1 if request.add_video_conferencing else 0,
                sendUpdates="all",  # Email the attendee
            ).execute()
        except HttpError as e:
            logger.error("Google event creation failed: %s", e)
            raise CalendarProviderError(f"Could not create Google Calendar event: {e.reason}")

        # Extract Meet link if created
        join_url = None
        if "conferenceData" in created and "entryPoints" in created["conferenceData"]:
            for ep in created["conferenceData"]["entryPoints"]:
                if ep.get("entryPointType") == "video":
                    join_url = ep.get("uri")
                    break

        return BookingConfirmation(
            booking_id=created["id"],
            confirmation_url=created.get("htmlLink"),
            join_url=join_url,
            confirmed_at=datetime.now(timezone.utc),
            requires_customer_confirmation=False,  # Google bookings are instant
        )

    async def health_check(self) -> bool:
        """Quick API call to verify the connection works."""
        try:
            cal_service = self._calendar_service()
            cal_service.calendarList().list(maxResults=1).execute()
            return True
        except Exception as e:
            logger.warning("Google health check failed: %s", e)
            return False
