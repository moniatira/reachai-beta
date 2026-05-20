"""Health check endpoint."""
from datetime import datetime, timezone
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "reachai-api",
        "version": "fbdf649+debug",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
