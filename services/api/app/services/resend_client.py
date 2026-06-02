"""Resend email client.

Resend's official SDK is simple but we wrap it so we can:
- Mock easily in tests
- Add retries/rate limiting later
- Switch to a different provider if needed
"""
import logging
from typing import Any

import httpx

from app.core.config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()


class EmailError(Exception):
    pass


async def send_email(
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    reply_to: str | None = None,
    attachments: list[dict] | None = None,
) -> dict[str, Any]:
    """Send a transactional email via Resend.

    Returns the Resend response dict (includes message id for tracking).
    Raises EmailError on any failure.

    attachments: list of {"filename": str, "content": str} where content is base64-encoded.
    """
    if not settings.resend_api_key:
        logger.error("RESEND_API_KEY not set — email not sent")
        raise EmailError("Email service not configured")

    payload = {
        "from": settings.from_email,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to
    if attachments:
        payload["attachments"] = attachments

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            logger.error("Resend network error: %s", e)
            raise EmailError(f"Email service unreachable: {e}")

    if resp.status_code >= 400:
        logger.error("Resend error %d: %s", resp.status_code, resp.text)
        raise EmailError(f"Resend returned {resp.status_code}: {resp.text[:200]}")

    return resp.json()
