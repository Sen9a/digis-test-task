"""
Integration test: full sync over HTTP using real connectors and PostgreSQL.

Requires fake APIs and PostgreSQL running:
    docker compose up -d

Run with:
    pytest tests/test_integration.py -v
"""

import pytest

from src.clients.api_client import APIClient
from src.connectors.source import SourceAPIConnector
from src.connectors.target import TargetAPIConnector
from src.db.engine import create_engine, create_session_factory, init_db
from src.services.aiohttp_service import AiohttpAPIService
from src.sync.engine import SyncEngine
from src.sync.pg_state import PostgresStateStore

SOURCE_URL = "http://localhost:8001"
TARGET_URL = "http://localhost:8002"


@pytest.fixture
async def state_store():
    """Create a BaseManager with its own engine, ensure tables exist."""
    engine = create_engine()
    await init_db(engine)
    session_factory = create_session_factory(engine)
    store = PostgresStateStore(session_factory=session_factory)
    await store.clear()
    yield store
    await store.clear()
    await engine.dispose()


@pytest.fixture
def source():
    service = AiohttpAPIService(base_url=SOURCE_URL)
    client = APIClient(service)
    return SourceAPIConnector(client)


@pytest.fixture
def target():
    service = AiohttpAPIService(base_url=TARGET_URL)
    client = APIClient(service)
    return TargetAPIConnector(client)


@pytest.fixture
async def engine(source, target, state_store):
    return SyncEngine(
        source=source,
        target=target,
        store=state_store,
        tenant_id="tenant-integration",
    )


@pytest.mark.integration
class TestIntegrationSync:
    """End-to-end sync tests using real HTTP calls to fake APIs."""

    async def test_full_sync_over_http(self, engine, state_store):
        """Full sync: fetch from source API → normalize → export to target API."""
        run = await engine.run(
            source_credentials={"api_key": "***"},
            target_credentials={"api_key": "***"},
            batch_size=10,
        )

        assert run.status.value == "completed"
        assert run.records_processed > 0
        assert run.records_succeeded > 0
        assert run.records_failed == 0

        # Verify state tracking
        states = await state_store.get_all_states("tenant-integration")
        assert len(states) == run.records_processed

        for state in states:
            assert state.status.value == "exported"
            assert state.target_record_id is not None

    async def test_sync_idempotency_over_http(self, engine, state_store):
        """Second sync should skip all unchanged invoices."""
        creds = {"api_key": "***"}

        # First run
        run1 = await engine.run(
            source_credentials=creds,
            target_credentials=creds,
            batch_size=10,
        )
        assert run1.records_succeeded > 0

        # Second run — all skipped
        run2 = await engine.run(
            source_credentials=creds,
            target_credentials=creds,
            batch_size=10,
        )
        assert run2.records_processed == run1.records_processed
        assert run2.records_skipped == run1.records_processed
        assert run2.records_succeeded == 0

    async def test_sync_with_bad_source_auth(self, engine):
        """Sync should fail with invalid source credentials."""
        run = await engine.run(
            source_credentials={"api_key": "invalid"},
            target_credentials={"api_key": "***"},
        )
        assert run.status.value == "failed"

    async def test_sync_with_bad_target_auth(self, engine):
        """Sync should fail with invalid target credentials."""
        run = await engine.run(
            source_credentials={"api_key": "test-key"},
            target_credentials={"api_key": "invalid"},
        )
        assert run.status.value == "failed"
