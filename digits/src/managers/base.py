from typing import Any
from dataclasses import dataclass
from src.db.engine import get_db_session

@dataclass
class BaseManager:
    """
    PostgreSQL-backed state store for sync state and run tracking.

    Uses SQLAlchemy ORM with async sessions. session_factory must be an
    async context manager yielding a session within a transaction
    (e.g. src.db.engine.get_db_session).
    """

    session_factory: Any = get_db_session