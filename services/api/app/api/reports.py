"""Reports endpoints — PDF downloads for appointments and conversations.

GET /v1/workspaces/{slug}/reports/appointments   → PDF of all bookings
GET /v1/workspaces/{slug}/reports/conversations  → PDF of all chat sessions

Both endpoints accept `token` as a query param as an alternative to the
Authorization: Bearer header (needed for browser download links).
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from fpdf import FPDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.jwt_utils import get_current_user_optional, decode_token, AuthError
from app.models import Workspace
from app.models.workspace import Booking, ChatSession, WorkspaceOwner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/workspaces", tags=["reports"])

BRAND_COLOR = (83, 74, 183)   # #534AB7 as RGB
HEADER_BG   = (240, 238, 254) # #F0EFFE
TEXT_DARK   = (26, 26, 26)
TEXT_MUTED  = (100, 100, 110)


class ReachAIPDF(FPDF):
    def __init__(self, title: str, workspace_name: str):
        super().__init__(orientation="L", unit="mm", format="A4")
        self._report_title = title
        self._workspace_name = workspace_name
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(auto=True, margin=14)

    def header(self):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*BRAND_COLOR)
        self.cell(0, 8, "ReachAI", ln=False)
        self.set_text_color(*TEXT_MUTED)
        self.set_font("Helvetica", "", 9)
        self.cell(0, 8, f"  ·  {self._workspace_name}", ln=True)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*TEXT_DARK)
        self.cell(0, 9, self._report_title, ln=True)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*TEXT_MUTED)
        now = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
        self.cell(0, 6, f"Generated {now}", ln=True)
        self.ln(3)
        # Thin rule
        self.set_draw_color(*BRAND_COLOR)
        self.set_line_width(0.4)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*TEXT_MUTED)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def table_header(self, cols: list[tuple[str, int]]):
        self.set_fill_color(*HEADER_BG)
        self.set_text_color(*BRAND_COLOR)
        self.set_font("Helvetica", "B", 8)
        for label, width in cols:
            self.cell(width, 7, label, border=0, fill=True, align="L")
        self.ln()
        self.set_draw_color(*BRAND_COLOR)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(1)

    def table_row(self, values: list[tuple[str, int]], shade: bool):
        if shade:
            self.set_fill_color(248, 248, 252)
        self.set_text_color(*TEXT_DARK)
        self.set_font("Helvetica", "", 8)
        # Use multi_cell for the first long column, plain cell for the rest
        x0, y0 = self.get_x(), self.get_y()
        row_h = 6
        for i, (val, width) in enumerate(values):
            self.set_xy(x0 + sum(w for _, w in values[:i]), y0)
            if shade:
                self.set_fill_color(248, 248, 252)
                self.cell(width, row_h, _clip(val, width), border=0, fill=True, align="L")
            else:
                self.cell(width, row_h, _clip(val, width), border=0, align="L")
        self.ln(row_h)
        # Subtle row separator
        self.set_draw_color(220, 220, 230)
        self.set_line_width(0.1)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())


def _clip(text: str, width_mm: int, char_per_mm: float = 0.45) -> str:
    """Truncate text so it fits roughly within `width_mm`."""
    limit = max(4, int(width_mm * char_per_mm))
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _fmt_dt(dt) -> str:
    if dt is None:
        return ""
    if hasattr(dt, "strftime"):
        return dt.strftime("%b %d %Y %H:%M")
    return str(dt)


async def _resolve_user(token_param, user_id_from_header):
    if user_id_from_header:
        return user_id_from_header
    if token_param:
        try:
            payload = decode_token(token_param, expected_type="session")
            return payload.get("sub")
        except (AuthError, Exception):
            return None
    return None


async def _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id):
    settings = get_settings()
    result = await db.execute(select(Workspace).where(Workspace.slug == slug))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    is_admin = x_admin_key and x_admin_key == settings.admin_api_key
    if is_admin:
        return workspace
    if not user_id:
        raise HTTPException(401, "Admin key or user session required")
    owner_check = await db.execute(
        select(WorkspaceOwner).where(
            WorkspaceOwner.workspace_id == workspace.id,
            WorkspaceOwner.user_id == user_id,
        )
    )
    if not owner_check.scalar_one_or_none():
        raise HTTPException(403, "You don't have access to this workspace")
    return workspace


@router.get("/{slug}/reports/appointments")
async def report_appointments(
    slug: str,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id_header: str | None = Depends(get_current_user_optional),
):
    """Download all bookings as a PDF."""
    user_id = await _resolve_user(token, user_id_header)
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(Booking)
        .where(Booking.workspace_id == workspace.id)
        .order_by(Booking.scheduled_for.desc())
    )
    bookings = result.scalars().all()

    pdf = ReachAIPDF("Appointments Report", workspace.name)
    pdf.add_page()

    cols = [
        ("Customer", 44),
        ("Email", 52),
        ("Service", 44),
        ("Channel", 20),
        ("Scheduled", 36),
        ("Duration", 22),
        ("Booked on", 36),
    ]
    pdf.table_header(cols)

    for i, b in enumerate(bookings):
        pdf.table_row([
            (b.customer_name, 44),
            (b.customer_email, 52),
            (b.service_name, 44),
            (b.channel, 20),
            (_fmt_dt(b.scheduled_for), 36),
            (f"{b.duration_minutes} min", 22),
            (_fmt_dt(b.created_at), 36),
        ], shade=(i % 2 == 1))

    if not bookings:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(0, 10, "No appointments recorded yet.", ln=True)

    buf = io.BytesIO(pdf.output())
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={slug}-appointments.pdf"},
    )


@router.get("/{slug}/reports/conversations")
async def report_conversations(
    slug: str,
    token: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
    user_id_header: str | None = Depends(get_current_user_optional),
):
    """Download all chat sessions as a PDF."""
    user_id = await _resolve_user(token, user_id_header)
    workspace = await _load_workspace_owner_or_admin(slug, db, x_admin_key, user_id)

    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.workspace_id == workspace.id)
        .order_by(ChatSession.created_at.desc())
    )
    sessions = result.scalars().all()

    pdf = ReachAIPDF("Conversations Report", workspace.name)
    pdf.add_page()

    cols = [
        ("Session ID", 44),
        ("Channel", 20),
        ("Customer", 40),
        ("Email", 52),
        ("Booked", 20),
        ("Messages", 22),
        ("Started", 36),
    ]
    pdf.table_header(cols)

    for i, s in enumerate(sessions):
        pdf.table_row([
            (s.id[:20], 44),
            (s.channel, 20),
            (s.customer_name or "—", 40),
            (s.customer_email or "—", 52),
            ("Yes" if s.booked else "No", 20),
            (str(len(s.messages) if s.messages else 0), 22),
            (_fmt_dt(s.created_at), 36),
        ], shade=(i % 2 == 1))

    if not sessions:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(0, 10, "No conversations recorded yet.", ln=True)

    buf = io.BytesIO(pdf.output())
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={slug}-conversations.pdf"},
    )
