"""Async SQLAlchemy database setup."""
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


settings = get_settings()


def _async_db_url(url: str) -> str:
    """Ensure the URL uses the asyncpg dialect.

    Railway and Render provide DATABASE_URL as ``postgresql://...`` — SQLAlchemy
    needs ``postgresql+asyncpg://...`` to use the async driver.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    _async_db_url(settings.database_url),
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a database session per request."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
