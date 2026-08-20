"""Pytest configuration: isolate tests on a dedicated database.

Redirects settings.database_url to a "<app_db>_test" database BEFORE any
src module is imported (engines are built from settings), and creates
that database if it doesn't exist. This keeps test runs from wiping
application state — the fixtures call manager.clear() which deletes
all rows.
"""

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg

import settings as settings_module


def _build_test_url() -> str:
    parts = urlsplit(settings_module.settings.database_url)
    test_db = parts.path.lstrip("/") + "_test"
    return urlunsplit(parts._replace(path=f"/{test_db}"))


TEST_DATABASE_URL = _build_test_url()

# Must happen before any src import reads settings.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
settings_module.settings.database_url = TEST_DATABASE_URL


async def _ensure_test_database() -> None:
    parts = urlsplit(TEST_DATABASE_URL)
    db_name = parts.path.lstrip("/")
    server_dsn = urlunsplit(parts._replace(scheme="postgresql", path="/postgres"))
    conn = await asyncpg.connect(server_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


asyncio.run(_ensure_test_database())
