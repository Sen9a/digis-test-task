"""Async database engine and session factory."""
from typing import AsyncIterator

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from settings import settings


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

engine = create_engine()
async_session_factory = create_session_factory(engine)

@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provides a transactional scope around a series of operations."""

    async with async_session_factory() as session:
        async with session.begin():
            yield session
