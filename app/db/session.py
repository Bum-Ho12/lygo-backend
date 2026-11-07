from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine for the application role (non-owner, no BYPASSRLS)."""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine or get_engine(), expire_on_commit=False)
