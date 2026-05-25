"""Calendar provider abstraction.

Public API: import from this module to get providers.

  from app.services.calendar import get_provider_for_workspace
  provider = await get_provider_for_workspace(workspace, db)
  slots = await provider.find_available_slots(...)
"""
from app.services.calendar.base import (
    BookingConfirmation,
    BookingRequest,
    CalendarProvider,
    CalendarProviderError,
    CalendarService,
    CalendarSlot,
    TokenExpiredError,
)
from app.services.calendar.registry import (
    PROVIDER_NAMES,
    get_provider_for_workspace,
    list_connections_for_workspace,
)

__all__ = [
    "BookingConfirmation",
    "BookingRequest",
    "CalendarProvider",
    "CalendarProviderError",
    "CalendarService",
    "CalendarSlot",
    "PROVIDER_NAMES",
    "TokenExpiredError",
    "get_provider_for_workspace",
    "list_connections_for_workspace",
]
