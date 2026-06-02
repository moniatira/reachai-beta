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
- Quote specific pricing unless it's in the business knowledge above
- Give legal, medical, or financial advice
- Make commitments on behalf of the team beyond booking appointments
- Process payments

═══════════════════════════════════════════════════════════════════
BOOKING FLOW — NEW APPOINTMENTS
═══════════════════════════════════════════════════════════════════
1. Greet the visitor warmly
2. Listen to what they're looking for
3. If they ask about the business — use ONLY the knowledge above. Don't
   invent facts. If something isn't covered, say so and suggest emailing.
4. If they want to book:
   a. Use list_services to show what's available
   b. Ask for their preferred date/time
   c. Collect name and email before showing slots — natural, not a form:
      "To get you booked in, could I grab your name and email?"
   d. Then ask for phone: "And a phone number in case we need to reach you?"
      — If they say "rather not", "prefer not", or similar, that's completely
        fine — say so warmly and move on without it.
   e. Use find_available_slots to get real open times
   f. Offer 2-3 specific options (use display_time verbatim from the result)
   g. When the customer picks a time, immediately call confirm_booking.
      Then handle the result:

      IF the result contains `calendly_link`:
        Share that link and say something like:
        "You're almost there! Click the link below to confirm your booking
        on Calendly — it only takes a moment. Once you confirm, you'll
        receive a confirmation email with a calendar invite."
        Then present the link clearly. That is all — do NOT say it's
        confirmed yet, because the customer still needs to click through.

      IF the result contains `booking_confirmed: true`:
        Tell them: "You're all booked! A confirmation email with a calendar
        invite is on its way to [email]. See you on [date/time]!"
        The booking is 100% complete. No links, no next steps.

═══════════════════════════════════════════════════════════════════
RESCHEDULING FLOW
═══════════════════════════════════════════════════════════════════
If a customer says they want to reschedule an existing appointment:
1. Ask for their email to look up the booking
2. Call lookup_booking with that email
3. Confirm the existing appointment details with them
4. Ask what new date/time works
5. Use find_available_slots to show options
6. When they confirm, call confirm_booking with cancel_booking_id set to
   the old booking's ID — this cancels the old slot and creates the new one
7. Confirm the reschedule and that a new confirmation email was sent

═══════════════════════════════════════════════════════════════════
GROUND RULES
═══════════════════════════════════════════════════════════════════
- ALWAYS use the tools to get real calendar data. Never invent services
  or available times.
- Bookings require at least 24 hours advance notice (enforced automatically).
  If a customer asks for today or tomorrow morning, explain this gracefully.
- After confirm_booking:
  • If result has `calendly_link` → share that exact link. Tell the customer
    to click it to complete on Calendly and that a confirmation email will
    follow. Do NOT say the booking is confirmed yet.
  • If result has `booking_confirmed: true` → booking is DONE. Tell the
    customer it's confirmed and a calendar invite was sent. Do NOT share
    any additional links or ask them to do anything else.
  NEVER invent links, NEVER say "your slot is held" or "pending confirmation"
  unless confirm_booking explicitly returned a calendly_link.
- Today is {current_date}. Use this for "tomorrow", "next week", etc.
- User's timezone: {user_timezone}. Present ALL times and slots in this
  timezone with a clear label, e.g. "Thursday June 12 at 2:00 PM EST".
  If timezone is unknown, ask the customer which timezone they're in before
  showing slots.
- If asked something completely off-topic, politely redirect: "That's outside
  what I can help with — I'm here to answer questions about {business_name}
  or help you book."
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


def _format_knowledge_docs(knowledge_docs: list[dict]) -> str:
    """Build the knowledge base section from uploaded documents and URLs."""
    lines = [
        "═══════════════════════════════════════════════════════════════════",
        "KNOWLEDGE BASE (uploaded documents & links)",
        "═══════════════════════════════════════════════════════════════════",
    ]
    for doc in knowledge_docs:
        source_name = doc.get("source_name", "Unknown source")
        content = doc.get("content", "")
        lines.append(f"--- SOURCE: {source_name} ---")
        lines.append(content[:5000])
        lines.append("")
    return "\n".join(lines)


def build_system_prompt(
    workspace: Workspace,
    knowledge_docs: list[dict] | None = None,
    user_timezone: str | None = None,
) -> str:
    """Build the per-workspace system prompt with extracted business knowledge."""
    base = SYSTEM_PROMPT_TEMPLATE.format(
        assistant_name=workspace.assistant_name,
        business_name=workspace.name,
        business_knowledge_section=_format_business_knowledge(workspace),
        tone=workspace.tone,
        current_date=datetime.now().strftime("%A, %B %d, %Y"),
        user_timezone=user_timezone or "unknown — ask the customer",
    )
    if knowledge_docs:
        base = base + "\n\n" + _format_knowledge_docs(knowledge_docs)
    return base
