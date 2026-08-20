"""
CLI entry point for running a single invoice sync.

Usage:
    python -m src.main

Environment variables:
    SOURCE_API_URL   — Source API base URL (default: http://localhost:8001)
    TARGET_API_URL   — Target API base URL (default: http://localhost:8002)
    TENANT_ID        — Tenant identifier (default: demo-tenant)
    SOURCE_API_KEY   — Source API key (default: test-key)
    TARGET_API_KEY   — Target API key (default: test-key)
    BATCH_SIZE       — Invoices per fetch batch (default: 10)
"""

import asyncio
import logging
import os
import sys

from src.clients.api_client import APIClient
from src.connectors.source import SourceAPIConnector
from src.connectors.target import TargetAPIConnector
from src.services.aiohttp_service import AiohttpAPIService
from src.sync.engine import SyncEngine
from src.sync.state import StateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> int:
    # Config from environment
    source_url = os.getenv("SOURCE_API_URL", "http://localhost:8001")
    target_url = os.getenv("TARGET_API_URL", "http://localhost:8002")
    tenant_id = os.getenv("TENANT_ID", "demo-tenant")
    source_key = os.getenv("SOURCE_API_KEY", "***")
    target_key = os.getenv("TARGET_API_KEY", "***")
    batch_size = int(os.getenv("BATCH_SIZE", "10"))

    logger.info("Starting sync: %s → %s (tenant: %s)", source_url, target_url, tenant_id)

    # Build connectors
    source = SourceAPIConnector(
        APIClient(AiohttpAPIService(base_url=source_url))
    )
    target = TargetAPIConnector(
        APIClient(AiohttpAPIService(base_url=target_url))
    )

    # Run sync
    store = StateStore()
    engine = SyncEngine(
        source=source,
        target=target,
        store=store,
        tenant_id=tenant_id,
        max_retries=3,
        retry_base_delay=1.0,
    )

    run = await engine.run(
        source_credentials={"api_key": source_key},
        target_credentials={"api_key": target_key},
        batch_size=batch_size,
    )

    # Print results
    print()
    print("=" * 60)
    print(f"  Sync Run: {run.id}")
    print("=" * 60)
    print(f"  Status:     {run.status.value}")
    print(f"  Duration:   {run.duration_seconds:.2f}s" if run.duration_seconds else "  Duration:   —")
    print(f"  Processed:  {run.records_processed}")
    print(f"  Succeeded:  {run.records_succeeded}")
    print(f"  Failed:     {run.records_failed}")
    print(f"  Skipped:    {run.records_skipped}")
    print("=" * 60)

    # Print state summary
    counts = store.count_states(tenant_id)
    if counts:
        print("\n  State breakdown:")
        for status, count in sorted(counts.items()):
            print(f"    {status}: {count}")

    # Print failed records if any
    if run.records_failed > 0:
        from src.models import SyncStateStatus
        failed = store.get_states_by_status(tenant_id, SyncStateStatus.FAILED)
        print("\n  Failed records:")
        for state in failed:
            print(f"    {state.source_record_id}: {state.last_error}")

    print()

    return 0 if run.status.value == "completed" else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
