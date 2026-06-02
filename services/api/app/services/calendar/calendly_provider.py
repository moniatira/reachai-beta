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
from app.services.calendly import _api_get, refresh_access_token, CalendlyError

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
        return False

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
        """Calendly doesn't support direct API booking — return a pre-auth scheduling link.

        If the slot's provider_metadata already has a scheduling_url (slot-specific),
        use that directly. Otherwise build one from the service's base booking_url +
        the slot start time so the customer lands on Calendly with that exact slot
        pre-selected.
        """
        scheduling_url = request.slot.provider_metadata.get("scheduling_url")
        if not scheduling_url:
            services = await self.list_services()
            service = next((s for s in services if s.id == request.service_id), None)
            base_url = service.booking_url if service else None

            if not base_url:
                raise CalendarProviderError("No Calendly scheduling URL available")

            # Append the slot time so Calendly pre-selects it for the customer
            slot_utc = request.slot.start.astimezone(timezone.utc)
            time_str = slot_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            scheduling_url = f"{base_url}/{time_str}"

        name_encoded = request.customer_name.replace(" ", "%20")
        prefilled = f"{scheduling_url}?name={name_encoded}&email={request.customer_email}"

        return BookingConfirmation(
            booking_id=f"calendly-pending-{request.slot.start.isoformat()}",
            confirmation_url=prefilled,
            join_url=None,
            confirmed_at=datetime.now(timezone.utc),
            requires_customer_confirmation=True,
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
