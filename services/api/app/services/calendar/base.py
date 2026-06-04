"""CalendarProvider — the unified interface all 3 calendar backends implement.

This is the abstraction that lets Sarah's chat code stay provider-agnostic.

When chat.py wants to find available slots, it doesn't care if the calendar
is Google, Outlook, or Calendly. It just calls provider.find_available_slots()
and gets back a list of CalendarSlot objects.

Adding Cal.com or Acuity in the future = create a new provider class
that implements this interface. No changes anywhere else.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class CalendarService:
    """A bookable service offered by the SMB.

    For Calendly: an event_type. For Google/Outlook: a "service" we infer
    or that the SMB configured manually (e.g. "30-min consultation").
    """

    id: str  # Provider-specific identifier
    name: str
    duration_minutes: int
    description: str | None = None
    price_cents: int | None = None
    location: str | None = None  # "video" | "in-person" | physical address
    requires_payment: bool = False
    booking_url: str | None = None  # Direct link if provider offers one


@dataclass
class CalendarSlot:
    """One available appointment slot."""

    start: datetime  # Timezone-aware
    end: datetime
    service_id: str  # Which service this slot is for
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    # provider_metadata holds anything the provider needs to remember about
    # this slot (e.g. Google calendar ID, Calendly availability_uri)


@dataclass
class BookingRequest:
    """What we need to create a booking on the provider."""

    service_id: str
    slot: CalendarSlot
    customer_name: str
    customer_email: str
    customer_phone: str | None = None
    notes: str | None = None
    add_video_conferencing: bool = True  # Auto-add Meet/Teams link if supported


@dataclass
class BookingConfirmation:
    """What the provider returns after creating a booking."""

    booking_id: str  # Provider-side ID
    confirmation_url: str | None = None  # Link the customer can click to confirm
    join_url: str | None = None  # Video conf link if applicable (Meet/Teams)
    confirmed_at: datetime = field(default_factory=lambda: datetime.utcnow())
    requires_customer_confirmation: bool = False
    # True for Calendly (sends a link, customer must click)
    # False for Google/Outlook (booking is created immediately)


class CalendarProviderError(Exception):
    """Base exception for provider errors."""


class TokenExpiredError(CalendarProviderError):
    """Raised when token refresh fails — workspace needs to reconnect."""


class CalendarProvider(ABC):
    """Abstract base. Each calendar backend implements these methods.

    Implementations:
      - GoogleCalendarProvider (services/calendar/google_provider.py)
      - OutlookCalendarProvider (services/calendar/outlook_provider.py)
      - CalendlyProvider (services/calendar/calendly_provider.py)
    """

    PROVIDER_NAME: str  # "google" | "outlook" | "calendly"

    @abstractmethod
    async def list_services(self) -> list[CalendarService]:
        """Return all bookable services for this workspace."""

    @abstractmethod
    async def find_available_slots(
        self,
        service_id: str,
        date_range_start: datetime,
        date_range_end: datetime,
        max_slots: int = 10,
    ) -> list[CalendarSlot]:
        """Return available slots for a service in the given range.

        Slots are returned in chronological order, max_slots cap applied.
        Empty list = no availability found.
        """

    @abstractmethod
    async def create_booking(self, request: BookingRequest) -> BookingConfirmation:
        """Create a booking. Returns confirmation with optional video link."""

    async def cancel_booking(self, provider_event_id: str) -> bool:
        """Cancel a booking on the provider. Returns True on success.

        Default is a no-op (returns False). Providers that support cancellation
        override this method.
        """
        return False

    @abstractmethod
    async def health_check(self) -> bool:
        """Quick test that the connection is working. Used by dashboard.

        Should make a low-cost API call (list 1 calendar, etc) and return
        True/False. Catches token expiration before customer-facing failures.
        """

    @property
    @abstractmethod
    def supports_video_conferencing(self) -> bool:
        """Whether this provider can auto-attach video links."""

    @property
    @abstractmethod
    def supports_real_time_booking(self) -> bool:
        """True if booking is instant (Google/Outlook). False if confirmation
        link is sent and customer must click (Calendly).
        """


def slots_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """Helper used by Google/Outlook providers to filter busy times."""
    return a_start < b_end and b_start < a_end


def generate_time_grid(
    start: datetime,
    end: datetime,
    slot_minutes: int,
    business_hours_start: int = 9,
    business_hours_end: int = 18,
    weekdays_only: bool = True,
) -> list[tuple[datetime, datetime]]:
    """Generate possible slot windows in business hours.

    Used by Google/Outlook providers: generate possible slots, then filter
    against busy events. Calendly handles this server-side via their API.
    """
    slots = []
    current = start.replace(minute=0, second=0, microsecond=0)

    while current < end:
        if weekdays_only and current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        if current.hour < business_hours_start:
            current = current.replace(hour=business_hours_start)
            continue
        if current.hour >= business_hours_end:
            current += timedelta(days=1)
            current = current.replace(hour=business_hours_start)
            continue

        slot_end = current + timedelta(minutes=slot_minutes)
        if slot_end.hour > business_hours_end:
            current += timedelta(days=1)
            current = current.replace(hour=business_hours_start)
            continue

        slots.append((current, slot_end))
        current = slot_end

    return slots
