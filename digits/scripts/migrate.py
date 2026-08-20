"""
Database migration script using Alembic.

Usage:
    python -m scripts.migrate          # Apply all pending migrations
    python -m scripts.migrate --stamp  # Stamp current state without running migrations

For more control, use alembic directly:
    poetry run alembic upgrade head
    poetry run alembic downgrade -1
    poetry run alembic revision --autogenerate -m "description"
    poetry run alembic history
"""

import subprocess
import sys


def migrate() -> None:
    result = subprocess.run(
        ["poetry", "run", "alembic", "upgrade", "head"],
        capture_output=False,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    migrate()
