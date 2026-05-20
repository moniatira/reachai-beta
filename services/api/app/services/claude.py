"""Anthropic Claude wrapper with tool use for the booking assistant.

Implements the agentic tool-calling loop:
1. Send conversation to Claude with available tools
2. If Claude wants to use a tool, execute it and feed result back
3. Loop until Claude returns a final text response

Tools the assistant has:
- list_services: Get bookable services from Calendly
- find_available_slots: Get open time slots for a service
- generate_booking_link: Get a customer-facing one-click booking URL
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Workspace
from app.prompts.booking import build_system_prompt
from app.services import calendly


settings = get_settings()
_client = AsyncAnthropic(api_key=settings.anthropic_api_key)


TOOLS: list[dict] = [
    {
        "name": "list_services",
        "description": (
            "Get the list of services this business offers. "
            "Use this when the customer asks 'what do you offer' or you "
            "need to know the available bookable event types."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_available_slots",
        "description": (
            "Find available appointment time slots for a specific service "
            "within a date range. Use this AFTER list_services to find when "
            "a service is available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type_uri": {
                    "type": "string",
                    "description": "The Calendly event type URI from list_services output",
                },
                "start_date": {
                    "type": "string",
                    "description": "Start of the search window in ISO format (e.g., 2026-05-21T00:00:00Z). Defaults to now.",
                },
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days forward to search. Use 7 for 'this week', 3 for 'next few days'.",
                    "default": 7,
                },
            },
            "required": ["event_type_uri"],
        },
    },
    {
        "name": "generate_booking_link",
        "description": (
            "Generate a one-click booking link for the customer. Pass the "
            "scheduling_url from a slot returned by find_available_slots."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scheduling_url": {
                    "type": "string",
                    "description": "The scheduling_url from the chosen slot",
                },
                "service_name": {
                    "type": "string",
                    "description": "Human-readable name of the service being booked",
                },
                "scheduled_for": {
                    "type": "string",
                    "description": "The chosen time in ISO format",
                },
            },
            "required": ["scheduling_url", "service_name", "scheduled_for"],
        },
    },
]


async def _execute_tool(
    db: AsyncSession,
    workspace: Workspace,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    """Run a single tool call and return its result for Claude."""
    try:
        if tool_name == "list_services":
            services = await calendly.list_event_types(db, workspace)
            if not services:
                return {"error": "No active services found. The business may not have set up Calendly event types yet."}
            return {
                "services": [
                    {
                        "uri": s["uri"],
                        "name": s["name"],
                        "duration_minutes": s["duration_minutes"],
                        "description": s["description"][:200] if s["description"] else "",
                    }
                    for s in services
                ]
            }

        if tool_name == "find_available_slots":
            event_type_uri = tool_input["event_type_uri"]
            start_raw = tool_input.get("start_date")
            days_ahead = tool_input.get("days_ahead", 7)

            if start_raw:
                start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            else:
                start = datetime.now(timezone.utc)
            end = start + timedelta(days=days_ahead)

            slots = await calendly.get_available_slots(
                db, workspace, event_type_uri, start, end
            )
            if not slots:
                return {
                    "slots": [],
                    "note": f"No openings in the next {days_ahead} days. Try a wider date range.",
                }
            return {
                "slots": [
                    {
                        "start_time": s["start_time"],
                        "scheduling_url": s["scheduling_url"],
                    }
                    for s in slots[:10]
                ]
            }

        if tool_name == "generate_booking_link":
            return {
                "booking_link": tool_input["scheduling_url"],
                "service_name": tool_input["service_name"],
                "scheduled_for": tool_input["scheduled_for"],
                "message": "Share this link with the customer. They click it to confirm in one step.",
            }

        return {"error": f"Unknown tool: {tool_name}"}

    except calendly.CalendlyError as e:
        return {"error": f"Calendar error: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


async def chat_turn(
    db: AsyncSession,
    workspace: Workspace,
    messages: list[dict],
    user_message: str,
) -> tuple[str, list[dict]]:
    """Process one user turn through Claude with tool use loop.

    Args:
        db: database session
        workspace: the SMB workspace
        messages: prior conversation history in Anthropic format
        user_message: the new user message

    Returns:
        (assistant_reply_text, updated_messages_list)
    """
    messages = messages + [{"role": "user", "content": user_message}]
    system = build_system_prompt(workspace)

    max_iterations = 6
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
                result = await _execute_tool(db, workspace, tu.name, dict(tu.input))
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": str(result)}
                )

            messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
            messages.append({"role": "user", "content": tool_results})
            continue

        text_blocks = [b.text for b in response.content if b.type == "text"]
        reply = "\n".join(text_blocks).strip()
        messages.append({"role": "assistant", "content": reply})
        return reply, messages

    fallback = "I'm having trouble right now. Could you try again in a moment?"
    messages.append({"role": "assistant", "content": fallback})
    return fallback, messages
