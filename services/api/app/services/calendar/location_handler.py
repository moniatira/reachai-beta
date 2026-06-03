"""Normalize Calendly location objects into a render-ready dict.

Calendly returns a `location` object on the scheduled_event resource.
The `type` field identifies the provider; each type exposes different
sub-fields (join_url, location text, phone number, etc.).

Usage:
    info = render_location(event_resource.get("location") or {})
    # info["label"]     → "Google Meet" / "123 Main St" / "Zoom" …
    # info["join_url"]  → "https://meet.google.com/…" or None
    # info["ics_location"] → string safe for ICS LOCATION field
"""
from __future__ import annotations


# Map Calendly type → (human label, emoji icon)
_TYPE_META: dict[str, tuple[str, str]] = {
    "google_conference":       ("Google Meet",           "📹"),
    "zoom_conference":         ("Zoom",                  "📹"),
    "microsoft_teams_conference": ("Microsoft Teams",    "📹"),
    "webex_conference":        ("Cisco Webex",           "📹"),
    "gotomeeting_conference":  ("GoToMeeting",           "📹"),
    "physical":                ("In-person",             "📍"),
    "inbound_call":            ("Phone call",            "📞"),
    "outbound_call":           ("Phone call",            "📞"),
    "custom":                  ("Custom",                "📍"),
    "ask_invitee":             ("TBD",                   "📍"),
}

_VIDEO_TYPES = {
    "google_conference",
    "zoom_conference",
    "microsoft_teams_conference",
    "webex_conference",
    "gotomeeting_conference",
}

_PHONE_TYPES = {"inbound_call", "outbound_call"}


def render_location(location: dict) -> dict:
    """Return a normalised, render-ready dict for any Calendly location type.

    Keys always present:
      label         str   — human-readable name ("Google Meet", "123 Main St")
      icon          str   — emoji suitable for email
      join_url      str|None — video/call link, or None
      address       str|None — physical address text, or None
      phone_number  str|None — for inbound/outbound calls
      is_video      bool
      is_phone      bool
      is_physical   bool
      available     bool  — False when Calendly returns status=="processing"
      ics_location  str   — safe string for ICS LOCATION field
    """
    if not location:
        return _empty()

    loc_type = location.get("type") or location.get("kind") or ""
    label, icon = _TYPE_META.get(loc_type, ("Location TBD", "📍"))

    join_url: str | None = location.get("join_url") or location.get("data", {}).get("join_url")
    address: str | None = location.get("location")  # Calendly uses "location" for address text
    phone_number: str | None = location.get("phone_number")

    # Some conference integrations report "processing" while the link is generated
    status = location.get("status", "")
    available = status != "processing"

    # Refine label for physical/call types using actual address/phone
    if loc_type == "physical" and address:
        label = address
    elif loc_type in _PHONE_TYPES and phone_number:
        label = phone_number
    elif loc_type == "custom" and address:
        label = address

    # ICS LOCATION: prefer join_url for video, address for physical, else label
    ics_location = join_url or address or phone_number or label

    return {
        "label": label,
        "icon": icon,
        "join_url": join_url,
        "address": address,
        "phone_number": phone_number,
        "is_video": loc_type in _VIDEO_TYPES,
        "is_phone": loc_type in _PHONE_TYPES,
        "is_physical": loc_type == "physical",
        "available": available,
        "ics_location": ics_location,
        "raw_type": loc_type,
    }


def _empty() -> dict:
    return {
        "label": "",
        "icon": "📅",
        "join_url": None,
        "address": None,
        "phone_number": None,
        "is_video": False,
        "is_phone": False,
        "is_physical": False,
        "available": True,
        "ics_location": "",
        "raw_type": "",
    }
