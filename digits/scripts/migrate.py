"""
One-time database migration script.

Usage:
    python -m scripts.migrate

Creates all tables if they don't exist.
Safe to run multiple times (idempotent).
"""

import asyncio
import logging

from src.db.engine import create_engine, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def migrate() -> None:
    engine = create_engine()
    try:
        await init_db(engine)
        logger.info("Database migration complete")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
