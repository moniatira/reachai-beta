"""Use Claude to extract structured business info from crawled website text.

Takes raw combined text from site_extractor and returns clean structured JSON
ready for the system prompt.

Cost: ~$0.02 per extraction (one-time per workspace).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.core.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()
_client = AsyncAnthropic(api_key=settings.anthropic_api_key)


EXTRACTION_PROMPT = """You are a business analyst extracting structured info from a website.

Below is text scraped from a small business's website. Your job is to extract
ONLY information that is clearly stated on the site — DO NOT make things up.

Return a JSON object with this exact schema:

{
  "business_summary": "2-3 sentence overview of what the business does and who it serves",
  "services": [
    {"name": "Service name", "description": "One-sentence description"}
  ],
  "target_customers": "1-2 sentences describing who they serve",
  "contact": {
    "email": "primary email or null",
    "phone": "primary phone or null",
    "address": "physical address or null"
  },
  "hours": "business hours as plain text, or null if not on site",
  "pricing_signals": "what the site says about pricing (e.g. 'free consultation', 'starts at $X', 'custom quotes') or null",
  "unique_value": "1-2 sentences on what makes them different — only if explicitly stated",
  "faqs": [
    {"question": "...", "answer": "..."}
  ],
  "confidence_notes": "any caveats about the extraction (e.g. 'minimal info on services page')"
}

RULES:
- If a field can't be filled from the text, use null (or [] for lists).
- Services: list ALL distinct services mentioned. Don't combine them.
- Don't infer hours, pricing, or contact details if not explicitly stated.
- Keep summaries short and factual.
- Use the same language/voice the business uses — don't add marketing fluff.

Return ONLY the JSON object. No markdown, no preamble, no trailing comments.

═══════════════════════════════════════════════════════════════
WEBSITE TEXT:
═══════════════════════════════════════════════════════════════

{site_text}"""


class SummarizerError(Exception):
    pass


def _extract_json_from_response(text: str) -> dict[str, Any]:
    """Claude sometimes wraps JSON in markdown fences despite our instructions."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract from markdown code fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the first {...} block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise SummarizerError(f"Could not parse JSON from Claude response: {text[:200]}")


async def extract_business_info(combined_site_text: str) -> dict[str, Any]:
    """Send the crawled text to Claude, get back structured business info."""
    if not combined_site_text or len(combined_site_text) < 200:
        raise SummarizerError("Site text too short to extract meaningful info")

    prompt = EXTRACTION_PROMPT.replace("{site_text}", combined_site_text)

    try:
        response = await _client.messages.create(
            model=settings.claude_model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise SummarizerError(f"Claude API call failed: {e}")

    if not response.content or not response.content[0].text:
        raise SummarizerError("Empty response from Claude")

    raw_text = response.content[0].text.strip()
    extracted = _extract_json_from_response(raw_text)

    # Validate top-level keys exist (fill nulls if missing)
    defaults = {
        "business_summary": None,
        "services": [],
        "target_customers": None,
        "contact": {"email": None, "phone": None, "address": None},
        "hours": None,
        "pricing_signals": None,
        "unique_value": None,
        "faqs": [],
        "confidence_notes": None,
    }
    for key, default in defaults.items():
        if key not in extracted:
            extracted[key] = default

    return extracted
