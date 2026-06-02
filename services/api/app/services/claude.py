"""Anthropic Claude wrapper with tool use for the booking assistant.

Implements the agentic tool-calling loop:
1. Send conversation to Claude with available tools
2. If Claude wants to use a tool, execute it and feed result back
3. Loop until Claude returns a final text response

Tools the assistant has:
- list_services: Get bookable services from the connected calendar
- find_available_slots: Get open time slots for a service (24h min advance enforced)
- confirm_booking: Collect customer info, create booking record, send ICS email
- lookup_booking: Find a customer's existing booking by email (for reschedule)
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Workspace, Booking
from app.models.workspace import KnowledgeDocument
from app.prompts.booking import build_system_prompt
from app.services.calendar.base import BookingRequest, CalendarSlot
from app.services.calendar.registry import get_provider_for_workspace


logger = logging.getLogger(__name__)
settings = get_settings()
_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

MIN_ADVANCE_HOURS = 24


TOOLS: list[dict] = [
    {
        "name": "list_services",
        "description": (
            "Get the list of bookable services this business offers. "
            "Call this when the customer asks what's available or before showing slots."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_available_slots",
        "description": (
            "Find available appointment slots for a specific service within a date range. "
            "Only slots at least 24 hours from now are returned. "
            "Call list_services first to get service IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {
                    "type": "string",
                    "description": "The service ID (URI) from list_services",
                },
                "start_date": {
                    "type": "string",
                    "description": "Search window start in ISO format (e.g. 2026-06-10T00:00:00Z). Defaults to now.",
                },
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days forward to search. Use 7 for 'this week', 14 for 'next two weeks'.",
                    "default": 7,
                },
            },
            "required": ["service_id"],
        },
    },
    {
        "name": "confirm_booking",
        "description": (
            "Confirm an appointment after collecting the customer's info. "
            "Confirm an appointment: creates a booking record and sends the customer a confirmation email with a .ics calendar invite. "
            "The booking is complete — no further action needed from the customer. "
            "You MUST have the customer's name and email before calling this. "
            "Phone is encouraged but optional."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Customer's full name"},
                "customer_email": {"type": "string", "description": "Customer's email address"},
                "customer_phone": {
                    "type": "string",
                    "description": "Customer's phone number. Use empty string if they declined.",
                },
                "service_id": {
                    "type": "string",
                    "description": "Service ID from list_services",
                },
                "service_name": {"type": "string", "description": "Human-readable service name"},
                "scheduled_for": {
                    "type": "string",
                    "description": "The chosen slot start time in ISO format from find_available_slots",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Appointment duration in minutes (from list_services)",
                },
                "scheduling_url": {
                    "type": "string",
                    "description": "The scheduling_url from the chosen slot (pass through from find_available_slots)",
                },
                "cancel_booking_id": {
                    "type": "string",
                    "description": "For reschedules only: the ID of the old booking to cancel (from lookup_booking)",
                },
            },
            "required": [
                "customer_name", "customer_email", "service_id",
                "service_name", "scheduled_for", "duration_minutes",
            ],
        },
    },
    {
        "name": "lookup_booking",
        "description": (
            "Look up a customer's existing upcoming booking by email. "
            "Use this when a customer wants to reschedule. "
            "Returns booking ID and details needed to cancel and rebook."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_email": {"type": "string", "description": "Customer's email address"},
            },
            "required": ["customer_email"],
        },
    },
]


async def _execute_tool(
    db: AsyncSession,
    workspace: Workspace,
    session_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    meta: dict,
) -> dict[str, Any]:
    """Run a single tool call and return its result for Claude."""
    try:
        if tool_name == "list_services":
            provider = await get_provider_for_workspace(workspace, db)
            if not provider:
                return {"error": "No calendar connected for this workspace."}
            services = await provider.list_services()
            if not services:
                return {"error": "No active services found. The business may not have set up any bookable services yet."}
            return {
                "services": [
                    {
                        "service_id": s.id,
                        "name": s.name,
                        "duration_minutes": s.duration_minutes,
                        "description": (s.description or "")[:200],
                    }
                    for s in services
                ]
            }

        if tool_name == "find_available_slots":
            service_id = tool_input["service_id"]
            start_raw = tool_input.get("start_date")
            days_ahead = tool_input.get("days_ahead", 7)

            now = datetime.now(timezone.utc)
            min_start = now + timedelta(hours=MIN_ADVANCE_HOURS)

            if start_raw:
                requested_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                start = max(requested_start, min_start)
            else:
                start = min_start

            end = start + timedelta(days=days_ahead)

            provider = await get_provider_for_workspace(workspace, db)
            if not provider:
                return {"error": "No calendar connected for this workspace."}

            slots = await provider.find_available_slots(service_id, start, end, max_slots=10)

            # Extra safety: strip any slots within 24h (provider may not enforce this)
            slots = [s for s in slots if s.start >= min_start]

            if not slots:
                return {
                    "slots": [],
                    "note": (
                        f"No openings found in the next {days_ahead} days "
                        f"(minimum 24h advance notice required). Try a wider range."
                    ),
                }
            return {
                "slots": [
                    {
                        "start_time": s.start.isoformat(),
                        "end_time": s.end.isoformat(),
                        "scheduling_url": s.provider_metadata.get("scheduling_url", ""),
                    }
                    for s in slots
                ]
            }

        if tool_name == "confirm_booking":
            customer_name = tool_input["customer_name"]
            customer_email = tool_input["customer_email"]
            customer_phone = tool_input.get("customer_phone") or None
            if customer_phone and not customer_phone.strip():
                customer_phone = None

            service_id = tool_input["service_id"]
            service_name = tool_input["service_name"]
            scheduled_for_str = tool_input["scheduled_for"]
            duration_minutes = int(tool_input["duration_minutes"])
            scheduling_url = tool_input.get("scheduling_url", "")
            cancel_booking_id = tool_input.get("cancel_booking_id")

            try:
                scheduled_for = datetime.fromisoformat(
                    scheduled_for_str.replace("Z", "+00:00")
                )
            except ValueError:
                return {"error": "Invalid scheduled_for format — use ISO 8601 (e.g. 2026-06-10T14:00:00Z)"}

            if scheduled_for.tzinfo is None:
                scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)

            # Enforce 24h advance notice
            if scheduled_for < datetime.now(timezone.utc) + timedelta(hours=MIN_ADVANCE_HOURS):
                return {"error": f"Bookings require at least {MIN_ADVANCE_HOURS} hours advance notice. Please choose a later time."}

            # Cancel old booking if rescheduling
            if cancel_booking_id:
                old = await db.get(Booking, cancel_booking_id)
                if old and old.workspace_id == workspace.id:
                    await db.delete(old)
                    logger.info("Deleted old booking %s for reschedule", cancel_booking_id)

            # Call the calendar provider to create the event — only for providers
            # that support real-time booking (Google, Outlook). Calendly requires
            # the customer to visit a scheduling URL which we no longer do.
            provider = await get_provider_for_workspace(workspace, db)
            join_url = None

            if provider and getattr(provider, "supports_real_time_booking", False):
                try:
                    slot = CalendarSlot(
                        start=scheduled_for,
                        end=scheduled_for + timedelta(minutes=duration_minutes),
                        service_id=service_id,
                        provider_metadata={"scheduling_url": scheduling_url},
                    )
                    req = BookingRequest(
                        service_id=service_id,
                        slot=slot,
                        customer_name=customer_name,
                        customer_email=customer_email,
                        customer_phone=customer_phone,
                    )
                    conf = await provider.create_booking(req)
                    if conf.join_url:
                        join_url = conf.join_url
                except Exception as e:
                    logger.warning("Provider create_booking failed, proceeding anyway: %s", e)

            # Save booking record
            booking = Booking(
                workspace_id=workspace.id,
                session_id=session_id,
                customer_name=customer_name,
                customer_email=customer_email,
                customer_phone=customer_phone,
                event_type_uri=service_id,
                service_name=service_name,
                scheduled_for=scheduled_for,
                duration_minutes=duration_minutes,
            )
            db.add(booking)
            await db.flush()

            # Build reschedule links for the confirmation email
            chat_url = workspace.website_url or None
            # scheduling_url here is the event-type booking page (e.g. Calendly link)
            reschedule_url = scheduling_url or None

            # Send confirmation email with .ics
            email_sent = False
            try:
                from app.services.email_templates import booking_confirmation_email
                from app.services.resend_client import send_email

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
                email_sent = True
            except Exception as e:
                logger.warning("Failed to send booking confirmation email: %s", e)

            # Propagate booking metadata back to the session
            meta["booked"] = True
            meta["customer_name"] = customer_name
            meta["customer_email"] = customer_email
            meta["customer_phone"] = customer_phone

            result: dict[str, Any] = {
                "booking_confirmed": True,
                "booking_id": booking.id,
                "service": service_name,
                "scheduled_for": scheduled_for.isoformat(),
                "duration_minutes": duration_minutes,
                "confirmation_email_sent": email_sent,
            }
            if join_url:
                result["video_link"] = join_url
            return result

        if tool_name == "lookup_booking":
            customer_email = tool_input["customer_email"]
            now = datetime.now(timezone.utc)

            result_rows = await db.execute(
                select(Booking)
                .where(
                    Booking.workspace_id == workspace.id,
                    Booking.customer_email == customer_email,
                    Booking.scheduled_for >= now,
                )
                .order_by(Booking.scheduled_for.asc())
                .limit(1)
            )
            booking = result_rows.scalar_one_or_none()

            if not booking:
                return {
                    "booking": None,
                    "note": f"No upcoming booking found for {customer_email} at this business.",
                }
            return {
                "booking": {
                    "id": booking.id,
                    "service_name": booking.service_name,
                    "service_id": booking.event_type_uri,
                    "scheduled_for": booking.scheduled_for.isoformat(),
                    "duration_minutes": booking.duration_minutes,
                    "customer_name": booking.customer_name,
                    "customer_email": booking.customer_email,
                    "customer_phone": booking.customer_phone or "",
                }
            }

        return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.exception("Tool %s raised an exception", tool_name)
        return {"error": f"Unexpected error in {tool_name}: {str(e)}"}


async def chat_turn(
    db: AsyncSession,
    workspace: Workspace,
    session_id: str,
    messages: list[dict],
    user_message: str,
    user_timezone: str | None = None,
) -> tuple[str, list[dict], dict]:
    """Process one user turn through Claude with tool use loop.

    Returns:
        (assistant_reply_text, updated_messages_list, meta)

    meta keys set by confirm_booking:
        booked, customer_name, customer_email, customer_phone
    """
    messages = messages + [{"role": "user", "content": user_message}]
    meta: dict = {}

    # Load knowledge base docs
    kb_result = await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.workspace_id == workspace.id)
        .order_by(KnowledgeDocument.created_at)
    )
    kb_docs = [
        {"source_name": d.source_name, "content": d.content}
        for d in kb_result.scalars()
    ]
    system = build_system_prompt(workspace, knowledge_docs=kb_docs if kb_docs else None, user_timezone=user_timezone)

    max_iterations = 8
    for _ in range(max_iterations):
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            tool_results = []
            for tu in tool_uses:
                result = await _execute_tool(
                    db, workspace, session_id, tu.name, dict(tu.input), meta
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": str(result)}
                )

            messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
            messages.append({"role": "user", "content": tool_results})
            continue

        text_blocks = [b.text for b in response.content if b.type == "text"]
        reply = "\n".join(text_blocks).strip()
        messages.append({"role": "assistant", "content": reply})
        return reply, messages, meta

    fallback = "I'm having trouble right now. Could you try again in a moment?"
    messages.append({"role": "assistant", "content": fallback})
    return fallback, messages, meta
