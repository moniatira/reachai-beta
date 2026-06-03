"""Calendly provider wrapper — implements CalendarProvider interface."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.core.security import decrypt_token, encrypt_token
from app.models.calendar_connection import CalendarConnection
from app.services.calendar.base import (
    BookingConfirmation,
    BookingRequest,
    CalendarProvider,
    CalendarProviderError,
    CalendarService,
    CalendarSlot,
)
from app.services.calendly import _api_get, _api_post, refresh_access_token, CalendlyError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


class CalendlyProvider(CalendarProvider):
    PROVIDER_NAME = "calendly"

    def __init__(self, connection: CalendarConnection, db: "AsyncSession | None" = None):
        self.connection = connection
        self._db = db

    @property
    def supports_video_conferencing(self) -> bool:
        return True

    @property
    def supports_real_time_booking(self) -> bool:
        return True

    async def _get_access_token(self) -> str:
        """Return a valid access token, refreshing from Calendly if expired."""
        now = datetime.now(timezone.utc)
        expires_at = self.connection.expires_at
        if expires_at is not None and expires_at <= now:
            raw_refresh = decrypt_token(self.connection.refresh_token_enc)
            try:
                refreshed = await refresh_access_token(raw_refresh)
            except CalendlyError as e:
                raise CalendarProviderError(f"Calendly token refresh failed: {e}")
            self.connection.access_token_enc = encrypt_token(refreshed["access_token"])
            self.connection.refresh_token_enc = encrypt_token(refreshed["refresh_token"])
            self.connection.expires_at = now + timedelta(
                seconds=refreshed.get("expires_in", 3600) - 60
            )
            if self._db:
                await self._db.commit()
        return decrypt_token(self.connection.access_token_enc)

    async def list_services(self) -> list[CalendarService]:
        """Calendly event types = our services."""
        try:
            access_token = await self._get_access_token()
            user_uri = self.connection.account_id
            data = await _api_get(
                access_token, "/event_types",
                params={"user": user_uri, "active": "true"},
            )
        except CalendarProviderError:
            raise
        except Exception as e:
            raise CalendarProviderError(f"Calendly list_event_types failed: {e}")

        services = []
        for et in data.get("collection", []):
            services.append(CalendarService(
                id=et["uri"],
                name=et["name"],
                duration_minutes=et.get("duration", 30),
                description=et.get("description_plain"),
                booking_url=et.get("scheduling_url"),
                location="video" if "video" in str(et.get("location", "")).lower() else None,
            ))
        return services

    async def find_available_slots(
        self,
        service_id: str,
        date_range_start: datetime,
        date_range_end: datetime,
        max_slots: int = 10,
    ) -> list[CalendarSlot]:
        """Use Calendly's availability API."""
        try:
            access_token = await self._get_access_token()
            data = await _api_get(
                access_token,
                "/event_type_available_times",
                params={
                    "event_type": service_id,
                    "start_time": date_range_start.isoformat(),
                    "end_time": date_range_end.isoformat(),
                },
            )
        except CalendarProviderError:
            raise
        except Exception as e:
            raise CalendarProviderError(f"Calendly availability fetch failed: {e}")

        services = await self.list_services()
        service = next((s for s in services if s.id == service_id), None)
        duration = service.duration_minutes if service else 30

        slots = []
        for window in data.get("collection", [])[:max_slots]:
            start_str = window.get("start_time")
            if not start_str:
                continue
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            slots.append(CalendarSlot(
                start=start,
                end=start + timedelta(minutes=duration),
                service_id=service_id,
                provider_metadata={
                    "scheduling_url": window.get("scheduling_url") or (
                        service.booking_url if service else None
                    ),
                },
            ))
        return slots

    async def create_booking(self, request: BookingRequest) -> BookingConfirmation:
        """Create a Calendly booking directly via POST /invitees (standard plan required)."""
        from urllib.parse import urlparse
        access_token = await self._get_access_token()
        slot_utc = request.slot.start.astimezone(timezone.utc)
        start_time = slot_utc.strftime("%Y-%m-%dT%H:%M:%S.000000Z")

        body: dict = {
            "event_type": request.service_id,
            "start_time": start_time,
            "invitee": {
                "name": request.customer_name,
                "email": request.customer_email,
                "timezone": "UTC",
            },
        }

        # Fetch event type to build the required location_configuration
        try:
            et_path = urlparse(request.service_id).path  # /event_types/<id>
            et_data = await _api_get(access_token, et_path)
            resource = et_data.get("resource", {})
            locations = resource.get("locations", [])
            logger.error(
                "Calendly event type locations for %s: %r", et_path, locations
            )
            if locations:
                loc = locations[0]
                kind = loc.get("kind") or loc.get("type", "")
                # "ask_invitee" means the invitee provides location — don't set it
                if kind and kind != "ask_invitee":
                    loc_config: dict = {"kind": kind}
                    for field in ("location", "additional_info", "phone_number"):
                        if loc.get(field):
                            loc_config[field] = loc[field]
                    body["event"] = {"location_configuration": loc_config}
                    logger.error("Calendly booking: using location_configuration=%r", loc_config)
                else:
                    logger.error("Calendly booking: skipping location (kind=%r)", kind)
        except Exception as e:
            logger.error("Could not fetch event type location config: %s", e, exc_info=True)

        logger.error("Calendly POST /invitees body (redacted): service=%r start=%r", request.service_id, start_time)

        try:
            data = await _api_post(access_token, "/invitees", body)
        except Exception as e:
            raise CalendarProviderError(f"Calendly direct booking failed: {e}")

        resource = data.get("resource", {})
        event_uri = resource.get("event", "")
        invitee_uri = resource.get("uri", f"calendly-invitee-{slot_utc.isoformat()}")
        reschedule_url = resource.get("reschedule_url")

        return BookingConfirmation(
            booking_id=event_uri or invitee_uri,
            confirmation_url=reschedule_url,
            join_url=None,
            confirmed_at=datetime.now(timezone.utc),
            requires_customer_confirmation=False,
        )

    async def health_check(self) -> bool:
        try:
            access_token = await self._get_access_token()
            user_uri = self.connection.account_id
            await _api_get(
                access_token, "/event_types",
                params={"user": user_uri, "active": "true", "count": "1"},
            )
            return True
        except Exception as e:
            logger.warning("Calendly health check failed: %s", e)
            return False
