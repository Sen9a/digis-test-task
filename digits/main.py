"""
CLI entry point for running a single invoice sync.

Usage:
    python -m main

Configuration via .env file or environment variables.
See src/settings.py for all available options.
"""

import asyncio
import logging
import sys

from src.clients.api_client import APIClient
from src.connectors.source import SourceAPIConnector
from src.connectors.target import TargetAPIConnector
from src.db.engine import init_db
from src.services.aiohttp_service import AiohttpAPIService
from settings import Settings
from src.sync.engine import SyncEngine
from src.sync.pg_state import PostgresStateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> int:
    settings = Settings()

    logger.info(
        "Starting sync: %s → %s (tenant: %s)",
        settings.source_api_url,
        settings.target_api_url,
        settings.tenant_id,
    )

    # Initialize database tables
    await init_db()
    logger.info("Database initialized")

    # Build connectors
    source = SourceAPIConnector(
        APIClient(AiohttpAPIService(base_url=settings.source_api_url))
    )
    target = TargetAPIConnector(
        APIClient(AiohttpAPIService(base_url=settings.target_api_url))
    )

    # Run sync
    store = PostgresStateStore()
    engine = SyncEngine(
        source=source,
        target=target,
        store=store,
        tenant_id=settings.tenant_id,
    )

    run = await engine.run(
        source_credentials={"api_key": settings.source_api_key},
        target_credentials={"api_key": settings.target_api_key},
        batch_size=settings.batch_size,
    )

    # Print results
    print()
    print("=" * 60)
    print(f"  Sync Run: {run.id}")
    print("=" * 60)
    print(f"  Status:     {run.status.value}")
    print(
        f"  Duration:   {run.duration_seconds:.2f}s"
        if run.duration_seconds
        else "  Duration:   —"
    )
    print(f"  Processed:  {run.records_processed}")
    print(f"  Succeeded:  {run.records_succeeded}")
    print(f"  Failed:     {run.records_failed}")
    print(f"  Skipped:    {run.records_skipped}")
    print("=" * 60)

    # Print state summary
    counts = await store.count_states(settings.tenant_id)
    if counts:
        print("\n  State breakdown:")
        for status, count in sorted(counts.items()):
            print(f"    {status}: {count}")

    # Print failed records if any
    if run.records_failed > 0:
        from src.models import SyncStateStatus

        failed = await store.get_states_by_status(
            settings.tenant_id, SyncStateStatus.FAILED
        )
        print("\n  Failed records:")
        for state in failed:
            print(f"    {state.source_record_id}: {state.last_error}")

    print()

    return 0 if run.status.value == "completed" else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
