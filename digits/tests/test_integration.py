"""
Integration test: full sync over HTTP using real connectors and PostgreSQL.

Requires fake APIs and PostgreSQL running:
    docker compose up -d

Run with:
    pytest tests/test_integration.py -v
"""

import logging
from contextlib import asynccontextmanager

import pytest

from src.clients.api_client import APIClient
from src.connectors.source import SourceAPIConnector
from src.connectors.target import TargetAPIConnector
from src.db import create_engine, create_session_factory, init_db
from src.managers import SyncRunManager, SyncStatesManager
from src.services import SyncRunService, SyncStateService
from src.services.aiohttp_service import AiohttpAPIService
from src.sync.engine import SyncEngine

SOURCE_URL = "http://localhost:8001"
TARGET_URL = "http://localhost:8002"


@pytest.fixture
async def db_engine():
    engine = create_engine()
    await init_db(engine)
    yield engine
    await engine.dispose()


def _make_session_provider(db_engine):
    """Wrap a session factory so it commits on exit, like get_db_session."""
    session_factory = create_session_factory(db_engine)

    @asynccontextmanager
    async def get_session():
        async with session_factory() as session:
            async with session.begin():
                yield session

    return get_session


@pytest.fixture
async def sync_state_service(db_engine):
    manager = SyncStatesManager(session_factory=_make_session_provider(db_engine))
    await manager.clear()
    service = SyncStateService(
        manager=manager,
        logger=logging.getLogger("test.sync_state"),
    )
    yield service
    await manager.clear()


@pytest.fixture
async def sync_run_service(db_engine):
    manager = SyncRunManager(session_factory=_make_session_provider(db_engine))
    await manager.clear()
    service = SyncRunService(
        manager=manager,
        logger=logging.getLogger("test.sync_run"),
    )
    yield service
    await manager.clear()


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
def engine(source, target, sync_run_service, sync_state_service):
    return SyncEngine(
        source=source,
        target=target,
        sync_run_service=sync_run_service,
        sync_state_service=sync_state_service,
        tenant_id="tenant-integration",
        logger=logging.getLogger("test.engine"),
    )


@pytest.mark.integration
class TestIntegrationSync:
    """End-to-end sync tests using real HTTP calls to fake APIs."""

    async def test_full_sync_over_http(self, engine, sync_state_service):
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
        states = await sync_state_service.get_all_states("tenant-integration")
        assert len(states) == run.records_processed

        for state in states:
            assert state.status.value == "exported"
            assert state.target_record_id is not None

    async def test_sync_idempotency_over_http(self, engine, sync_state_service):
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
