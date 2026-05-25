"""Calendly provider wrapper — implements CalendarProvider interface.

Wraps the existing Calendly service code (app/services/calendly.py) so it can
be used through the same abstraction as Google/Outlook. No behavior change
for existing Sambaluk/demo-salon customers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.models.calendar_connection import CalendarConnection
from app.services.calendar.base import (
    BookingConfirmation,
    BookingRequest,
    CalendarProvider,
    CalendarProviderError,
    CalendarService,
    CalendarSlot,
)
# Existing Calendly service module — kept in place
from app.services import calendly as legacy_calendly


logger = logging.getLogger(__name__)


class CalendlyProvider(CalendarProvider):
    PROVIDER_NAME = "calendly"

    def __init__(self, connection: CalendarConnection):
        self.connection = connection

    @property
    def supports_video_conferencing(self) -> bool:
        # Calendly supports video links but they're configured per event type
        # in the Calendly UI — we don't control whether one is attached
        return True

    @property
    def supports_real_time_booking(self) -> bool:
        # Calendly bookings require the customer to click a confirmation link
        return False

    async def list_services(self) -> list[CalendarService]:
        """Calendly event types = our services."""
        try:
            event_types = await legacy_calendly.list_event_types(self.connection)
        except Exception as e:
            raise CalendarProviderError(f"Calendly list_event_types failed: {e}")

        services = []
        for et in event_types:
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
            availability = await legacy_calendly.fetch_event_type_availability(
                self.connection,
                event_type_uri=service_id,
                start_time=date_range_start,
                end_time=date_range_end,
            )
        except Exception as e:
            raise CalendarProviderError(f"Calendly availability fetch failed: {e}")

        services = await self.list_services()
        service = next((s for s in services if s.id == service_id), None)
        duration = service.duration_minutes if service else 30

        slots = []
        for window in availability.get("collection", [])[:max_slots]:
            start_str = window.get("start_time")
            if not start_str:
                continue
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            slots.append(CalendarSlot(
                start=start,
                end=start + timedelta(minutes=duration),
                service_id=service_id,
                provider_metadata={
                    "scheduling_url": window.get("scheduling_url") or (service.booking_url if service else None),
                },
            ))
        return slots

    async def create_booking(self, request: BookingRequest) -> BookingConfirmation:
        """Calendly doesn't allow direct booking via API — we return the
        scheduling link so the customer clicks to confirm."""
        scheduling_url = request.slot.provider_metadata.get("scheduling_url")
        if not scheduling_url:
            # Fall back to building the URL from service metadata
            services = await self.list_services()
            service = next((s for s in services if s.id == request.service_id), None)
            scheduling_url = service.booking_url if service else None

        if not scheduling_url:
            raise CalendarProviderError("No Calendly scheduling URL available")

        # Calendly supports prefilling via URL params
        prefilled = (
            f"{scheduling_url}"
            f"?name={request.customer_name.replace(' ', '%20')}"
            f"&email={request.customer_email}"
        )

        return BookingConfirmation(
            booking_id=f"calendly-pending-{request.slot.start.isoformat()}",
            confirmation_url=prefilled,
            join_url=None,
            confirmed_at=datetime.now(timezone.utc),
            requires_customer_confirmation=True,
        )

    async def health_check(self) -> bool:
        try:
            await legacy_calendly.list_event_types(self.connection)
            return True
        except Exception as e:
            logger.warning("Calendly health check failed: %s", e)
            return False
