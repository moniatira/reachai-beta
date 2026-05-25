"""JWT encode/decode helpers for session tokens.

Sessions are stateless JWTs. Logout = client deletes the token.
For high-security operations we check the database too.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Header, HTTPException, status

from app.core.config import get_settings


settings = get_settings()


SESSION_EXPIRY_DAYS = 7
MAGIC_LINK_EXPIRY_MINUTES = 15


class AuthError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def create_session_token(user_id: str, email: str) -> str:
    """Issue a session JWT for an authenticated user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=SESSION_EXPIRY_DAYS)).timestamp()),
        "type": "session",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_magic_token(email: str) -> str:
    """Issue a short-lived magic link token."""
    now = datetime.now(timezone.utc)
    payload = {
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES)).timestamp()),
        "type": "magic",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    """Decode + validate a JWT. Raises AuthError on any failure."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AuthError("Token expired — please sign in again")
    except jwt.InvalidTokenError:
        raise AuthError("Invalid token")

    if payload.get("type") != expected_type:
        raise AuthError(f"Wrong token type (expected {expected_type})")

    return payload


def get_current_user_id(authorization: str | None = Header(None)) -> str:
    """FastAPI dependency to extract user_id from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_token(token, expected_type="session")

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Token missing subject")

    return user_id


def get_current_user_optional(authorization: str | None = Header(None)) -> str | None:
    """Like get_current_user_id but returns None instead of raising."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token = authorization.removeprefix("Bearer ").strip()
        payload = decode_token(token, expected_type="session")
        return payload.get("sub")
    except AuthError:
        return None
