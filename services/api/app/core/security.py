"""Security utilities: admin auth, token encryption."""
import base64
import hashlib
from cryptography.fernet import Fernet
from fastapi import Header, HTTPException, status

from app.core.config import get_settings


settings = get_settings()


def _derive_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from any secret string."""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_key(settings.session_secret_key))


def encrypt_token(plaintext: str) -> str:
    """Encrypt a value (e.g. OAuth access token) for at-rest storage."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a previously-encrypted value."""
    return _fernet.decrypt(ciphertext.encode()).decode()


def require_admin(x_admin_key: str = Header(None, alias="X-Admin-Key")) -> None:
    """Reject the request unless the admin API key is present and valid."""
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin key",
        )
