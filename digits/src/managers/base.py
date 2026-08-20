from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from src.db import create_engine, create_session_factory

class BaseManager:
    """
    PostgreSQL-backed state store for sync state and run tracking.

    Uses SQLAlchemy Core (not ORM) with async sessions.
    Converts between database rows and Pydantic models.
    """

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        if session_factory is not None:
            self._session_factory = session_factory
        elif engine is not None:
            self._session_factory = create_session_factory(engine)
        else:
            _engine = create_engine()
            self._session_factory = create_session_factory(_engine)