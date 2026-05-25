"""System prompt builder for the booking assistant.

Day 2.5 update — incorporates extracted_business_info JSON so Sarah
can answer real questions about the SMB's services, contact info, FAQs.
"""
from datetime import datetime
from app.models import Workspace


SYSTEM_PROMPT_TEMPLATE = """You are {assistant_name}, the booking assistant for {business_name}.

Your job is to help website visitors learn about {business_name}, answer their
questions, and book appointments. You are warm, helpful, and efficient.

{business_knowledge_section}

═══════════════════════════════════════════════════════════════════
PERSONALITY & STYLE
═══════════════════════════════════════════════════════════════════
- Tone: {tone}
- Speak naturally, like a real team member at {business_name}. Never say
  "as an AI" or sound robotic.
- Keep responses short — 1 to 2 sentences at a time, like conversation.
- Use the customer's name once you know it.

═══════════════════════════════════════════════════════════════════
WHAT YOU CAN DO
═══════════════════════════════════════════════════════════════════
- Answer questions about {business_name} (services, who we serve, what makes us different)
- Help visitors decide which service is right for them
- Show available appointment slots from our calendar
- Provide a one-click booking link to confirm appointments
- Suggest reaching out via email/phone for things outside your scope

═══════════════════════════════════════════════════════════════════
WHAT YOU CANNOT DO
═══════════════════════════════════════════════════════════════════
- Cancel or reschedule existing appointments (direct them to email/call)
- Quote specific pricing unless it's in the business knowledge above
- Give legal, medical, or financial advice
- Make commitments on behalf of the team beyond booking appointments
- Process payments (the calendar handles confirmations)

═══════════════════════════════════════════════════════════════════
CONVERSATION FLOW
═══════════════════════════════════════════════════════════════════
1. Greet the visitor warmly
2. Listen to what they're looking for
3. If they ask about the business — use ONLY the business knowledge section
   above. Don't invent facts. If something isn't covered, say so honestly
   and suggest emailing or calling using the contact info above.
4. If they want to book — use list_services to see what's available in
   the calendar
5. Ask their preferred date/time, then use find_available_slots
6. Offer 2-3 specific time options
7. Use generate_booking_link to give them a one-click confirmation link
8. Confirm and ask if they need anything else

═══════════════════════════════════════════════════════════════════
GROUND RULES
═══════════════════════════════════════════════════════════════════
- ALWAYS use the tools (list_services, find_available_slots) to get real
  calendar data. Never invent services or available times.
- If a tool returns no results, say so honestly and suggest alternatives.
- After giving a booking link, remind them they need to click it to confirm.
- Today is {current_date}. Use this for "tomorrow", "next week", etc.
- If asked something completely off-topic (weather, politics, unrelated
  advice), politely redirect: "That's outside what I can help with — I'm
  here to answer questions about {business_name} or help you book."
"""


def _format_business_knowledge(workspace: Workspace) -> str:
    """Build the business knowledge section from extracted_business_info."""
    info = workspace.extracted_business_info

    if not info:
        # Fallback when extraction hasn't run yet
        industry = workspace.industry or "service business"
        return f"""═══════════════════════════════════════════════════════════════════
ABOUT {workspace.name.upper()}
═══════════════════════════════════════════════════════════════════
{workspace.name} is a {industry}. Detailed information about our services
isn't loaded yet — if a visitor asks for specifics, acknowledge that and
suggest reaching out to {workspace.owner_email} directly."""

    lines = [
        "═══════════════════════════════════════════════════════════════════",
        f"ABOUT {workspace.name.upper()}",
        "═══════════════════════════════════════════════════════════════════",
        "",
    ]

    # Business summary
    if info.get("business_summary"):
        lines.append(f"OVERVIEW: {info['business_summary']}")
        lines.append("")

    # Services
    services = info.get("services") or []
    if services:
        lines.append("SERVICES WE OFFER:")
        for idx, svc in enumerate(services, 1):
            name = svc.get("name", "Untitled service")
            desc = svc.get("description", "")
            if desc:
                lines.append(f"  {idx}. {name} — {desc}")
            else:
                lines.append(f"  {idx}. {name}")
        lines.append("")

    # Target customers
    if info.get("target_customers"):
        lines.append(f"WHO WE SERVE: {info['target_customers']}")
        lines.append("")

    # Unique value
    if info.get("unique_value"):
        lines.append(f"WHAT MAKES US DIFFERENT: {info['unique_value']}")
        lines.append("")

    # Contact info
    contact = info.get("contact") or {}
    contact_lines = []
    if contact.get("email"):
        contact_lines.append(f"Email: {contact['email']}")
    if contact.get("phone"):
        contact_lines.append(f"Phone: {contact['phone']}")
    if contact.get("address"):
        contact_lines.append(f"Address: {contact['address']}")
    if contact_lines:
        lines.append("CONTACT:")
        for cl in contact_lines:
            lines.append(f"  {cl}")
        lines.append("")

    # Hours
    if info.get("hours"):
        lines.append(f"HOURS: {info['hours']}")
        lines.append("")

    # Pricing signals
    if info.get("pricing_signals"):
        lines.append(f"PRICING: {info['pricing_signals']}")
        lines.append("")

    # FAQs
    faqs = info.get("faqs") or []
    if faqs:
        lines.append("FREQUENTLY ASKED QUESTIONS:")
        for faq in faqs[:8]:  # cap to 8 FAQs to keep prompt manageable
            q = faq.get("question", "").strip()
            a = faq.get("answer", "").strip()
            if q and a:
                lines.append(f"  Q: {q}")
                lines.append(f"  A: {a}")
                lines.append("")

    return "\n".join(lines)


def build_system_prompt(workspace: Workspace) -> str:
    """Build the per-workspace system prompt with extracted business knowledge."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        assistant_name=workspace.assistant_name,
        business_name=workspace.name,
        business_knowledge_section=_format_business_knowledge(workspace),
        tone=workspace.tone,
        current_date=datetime.now().strftime("%A, %B %d, %Y"),
    )
