"""FastAPI application entry point for ReachAI beta."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.api import auth, calendar_status, calendly_oauth, chat, conversations, debug, google_oauth, health, knowledge, onboarding, outlook_oauth, reports, services, site_extract, version, widget, workspaces
from app.core.config import get_settings


settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ReachAI API starting · environment=%s", settings.environment)
    logger.info("CORS origins: %s", settings.cors_origins)
    yield
    logger.info("ReachAI API shutting down")


app = FastAPI(
    title="ReachAI API",
    description="Self-serve AI front desk for SMBs - beta",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)


app.include_router(health.router)
app.include_router(workspaces.router)
app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(site_extract.router)
app.include_router(google_oauth.router)
app.include_router(outlook_oauth.router)
app.include_router(calendar_status.router)
app.include_router(calendly_oauth.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(services.router)
app.include_router(conversations.router)
app.include_router(reports.router)
app.include_router(widget.router)
app.include_router(debug.router)
app.include_router(version.router)


@app.get("/v1/ping")
async def ping():
    import os
    from app.core.config import get_settings
    s = get_settings()
    return {
        "calendly_client_id_set": bool(s.calendly_client_id),
        "calendly_redirect_uri": s.calendly_redirect_uri,
        "env_CALENDLY_CLIENT_ID": os.environ.get("CALENDLY_CLIENT_ID", "NOT_SET"),
        "env_CALENDLY_REDIRECT_URI": os.environ.get("CALENDLY_REDIRECT_URI", "NOT_SET"),
    }


@app.get("/")
async def root():
    return {
        "service": "ReachAI API",
        "status": "ok",
        "version": "0.1.0",
        "docs": "/docs" if settings.environment != "production" else "disabled-in-production",
    }


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": "Validation error", "errors": exc.errors()},
    )
