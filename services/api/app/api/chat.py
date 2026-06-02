"""Chat endpoint - the widget calls this for every customer message."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import ChatSession, Workspace
from app.services.claude import chat_turn


router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatRequest(BaseModel):
    workspace_slug: str = Field(..., description="The workspace identifier (data-workspace attribute)")
    session_id: str | None = Field(None, description="Continue an existing session; omit to start new")
    message: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    workspace_name: str
    assistant_name: str


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle one customer message and return the assistant's reply."""
    result = await db.execute(
        select(Workspace).where(
            Workspace.slug == payload.workspace_slug,
            Workspace.active == True,
            Workspace.whitelisted == True,
        )
    )
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found or not active")

    await db.refresh(workspace, ["calendly_token"])
    if not workspace.calendly_token:
        raise HTTPException(
            status_code=400,
            detail=f"{workspace.name} hasn't connected their calendar yet.",
        )

    if payload.session_id:
        session_result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == payload.session_id,
                ChatSession.workspace_id == workspace.id,
            )
        )
        session = session_result.scalar_one_or_none()
        if not session:
            session = ChatSession(workspace_id=workspace.id, messages=[])
            db.add(session)
    else:
        session = ChatSession(workspace_id=workspace.id, messages=[])
        db.add(session)

    await db.flush()

    try:
        reply, updated_messages, meta = await chat_turn(
            db, workspace, session.id, session.messages, payload.message
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")

    session.messages = updated_messages
    if meta.get("booked"):
        session.booked = True
    if meta.get("customer_name"):
        session.customer_name = meta["customer_name"]
    if meta.get("customer_email"):
        session.customer_email = meta["customer_email"]
    if meta.get("customer_phone"):
        session.customer_phone = meta["customer_phone"]

    await db.commit()

    return ChatResponse(
        session_id=session.id,
        reply=reply,
        workspace_name=workspace.name,
        assistant_name=workspace.assistant_name,
    )
