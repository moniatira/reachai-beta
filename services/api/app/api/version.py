"""Version endpoint - proves which commit is running."""
from fastapi import APIRouter
router = APIRouter()
COMMIT_MARKER = "DEPLOY-MARKER-v2"

@router.get("/v1/version")
async def version():
    return {"marker": COMMIT_MARKER, "build": "fresh"}
