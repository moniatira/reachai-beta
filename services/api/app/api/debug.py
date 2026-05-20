"""TEMPORARY debug endpoint - remove after beta validation."""
from fastapi import APIRouter, Depends
from app.core.config import get_settings
from app.core.security import require_admin
import os

router = APIRouter(prefix="/v1/debug", tags=["debug"])

@router.get("/config", dependencies=[Depends(require_admin)])
async def show_config():
    s = get_settings()
    return {
        "via_settings": {
            "environment": s.environment,
            "calendly_client_id_present": bool(s.calendly_client_id),
            "calendly_client_id_prefix": s.calendly_client_id[:10] if s.calendly_client_id else "EMPTY",
            "calendly_redirect_uri": s.calendly_redirect_uri,
            "claude_model": s.claude_model,
        },
        "via_os_environ": {
            "CALENDLY_CLIENT_ID_present": "CALENDLY_CLIENT_ID" in os.environ,
            "CALENDLY_REDIRECT_URI_value": os.environ.get("CALENDLY_REDIRECT_URI", "NOT_SET"),
            "ENVIRONMENT_value": os.environ.get("ENVIRONMENT", "NOT_SET"),
        },
        "dotenv_file_exists": os.path.exists(".env"),
    }
