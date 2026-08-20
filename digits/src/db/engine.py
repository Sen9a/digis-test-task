"""Async database engine and session factory."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from settings import settings
from src.db.sql_metadata import metadata


def create_engine() -> AsyncEngine:
    """Create a new async engine."""
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=5,
        max_overflow=10,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the given engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create all tables if they don't exist."""
    if engine is None:
        engine = create_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
