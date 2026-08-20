from sqlalchemy.ext.asyncio import AsyncEngine

from .engine import create_engine
from src.tables.base import Base

async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create all tables if they don't exist."""
    if engine is None:
        engine = create_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)