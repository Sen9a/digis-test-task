"""
CLI entry point for running a single invoice sync.

Usage:
    python -m main

Configuration via .env file or environment variables.
See settings.py for all available options.
"""

import asyncio
import logging
import sys

from src.clients.api_client import APIClient
from src.connectors.source import SourceAPIConnector
from src.connectors.target import TargetAPIConnector
from src.managers import SyncRunManager, SyncStatesManager
from src.services import SyncRunService, SyncStateService
from src.services.aiohttp_service import AiohttpAPIService
from settings import  settings
from src.sync.engine import SyncEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> int:

    logger.info(
        "Starting sync: %s → %s (tenant: %s)",
        settings.source_api_url,
        settings.target_api_url,
        settings.tenant_id,
    )

    # Build connectors
    source = SourceAPIConnector(
        APIClient(AiohttpAPIService(base_url=settings.source_api_url))
    )
    target = TargetAPIConnector(
        APIClient(AiohttpAPIService(base_url=settings.target_api_url))
    )

    # Build services
    sync_run_service = SyncRunService(
        manager=SyncRunManager(),
        logger=logging.getLogger("sync_run"),
    )
    sync_state_service = SyncStateService(
        manager=SyncStatesManager(),
        logger=logging.getLogger("sync_state"),
    )

    # Run sync
    engine = SyncEngine(
        source=source,
        target=target,
        sync_run_service=sync_run_service,
        sync_state_service=sync_state_service,
        tenant_id=settings.tenant_id,
        logger=logging.getLogger("sync_engine"),
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
    counts = await sync_state_service.count_states(settings.tenant_id)
    if counts:
        print("\n  State breakdown:")
        for status, count in sorted(counts.items()):
            print(f"    {status}: {count}")

    # Print failed records if any
    if run.records_failed > 0:
        from src.models import SyncStateStatus

        failed = await sync_state_service.get_states_by_status(
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
