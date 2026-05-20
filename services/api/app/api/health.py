"""Health check endpoint."""
import os
from datetime import datetime, timezone
from fastapi import APIRouter
from app.core.config import get_settings

router = APIRouter()


@router.get("/health")
async def health():
    s = get_settings()
    return {
        "status": "ok",
        "service": "reachai-api",
        "version": "ce71517",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "env_check": {
            "calendly_client_id_set": bool(s.calendly_client_id),
            "calendly_redirect_uri": s.calendly_redirect_uri,
            "raw_CALENDLY_REDIRECT_URI": os.environ.get("CALENDLY_REDIRECT_URI", "NOT_SET"),
            "raw_CALENDLY_CLIENT_ID": os.environ.get("CALENDLY_CLIENT_ID", "NOT_SET"),
        },
    }
