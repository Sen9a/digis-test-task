"""Tests for the sync engine."""

import logging
from decimal import Decimal

import pytest

from src.clients.api_client import APIClient
from src.connectors.source import SourceAPIConnector
from src.connectors.target import TargetAPIConnector
from src.const import ErrorCategory, Status
from src.db.engine import create_engine, create_session_factory, init_db
from exceptions import RateLimitError, RetryableExportError
from src.managers import SyncRunManager, SyncStatesManager
from src.models import (
    ExportResult,
    SyncError,
    SyncStateStatus,
    SyncRunStatus,
)
from src.services import SyncRunService, SyncStateService
from src.services.fake_service import FakeAPIService
from src.sync.engine import SyncEngine
from src.utils import wait_retry_after_aware
from settings import settings

AUTH_RESPONSE = {"token": "***", "expires_in": 3600}

SAMPLE_INVOICES = [
    {
        "id": "inv-001",
        "number": "INV-1001",
        "customer": {"id": "cust-1", "name": "Acme Corp", "email": "billing@acme.com"},
        "amount": 100.00,
        "tax": 20.00,
        "currency": "usd",
        "status": "sent",
        "date": "2024-01-15",
        "due": "2024-02-15",
        "updated": "2024-01-15T10:30:00Z",
    },
    {
        "id": "inv-002",
        "number": "INV-1002",
        "customer": {"id": "cust-2", "name": "Globex Inc"},
        "amount": 250.50,
        "tax": 50.10,
        "currency": "eur",
        "status": "paid",
        "date": "2024-01-16",
        "updated": "2024-01-16T14:20:00Z",
    },
    {
        "id": "inv-003",
        "number": "INV-1003",
        "customer": "cust-3",
        "amount": 75.00,
        "tax": 0.00,
        "currency": "USD",
        "status": "draft",
        "date": "2024-01-17",
    },
]


def _make_source(service: FakeAPIService, invoices=None):
    """Create a SourceAPIConnector backed by FakeAPIService."""
    invoices = invoices or SAMPLE_INVOICES
    service.add_response("POST", "/auth/token", AUTH_RESPONSE)
    service.add_response(
        "GET",
        "/invoices",
        {"invoices": invoices, "next_cursor": None},
    )
    client = APIClient(service)
    return SourceAPIConnector(client)


def _make_target(service: FakeAPIService):
    """Create a TargetAPIConnector backed by FakeAPIService."""
    service.add_response("POST", "/auth/token", AUTH_RESPONSE)
    client = APIClient(service)
    return TargetAPIConnector(client)


@pytest.fixture
async def db_engine():
    engine = create_engine()
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sync_state_service(db_engine):
    session_factory = create_session_factory(db_engine)
    manager = SyncStatesManager(session_factory=session_factory)
    await manager.clear()
    service = SyncStateService(
        manager=manager,
        logger=logging.getLogger("test.sync_state"),
    )
    yield service
    await manager.clear()


@pytest.fixture
async def sync_run_service(db_engine):
    session_factory = create_session_factory(db_engine)
    manager = SyncRunManager(session_factory=session_factory)
    await manager.clear()
    service = SyncRunService(
        manager=manager,
        logger=logging.getLogger("test.sync_run"),
    )
    yield service
    await manager.clear()


@pytest.fixture
def source_service():
    return FakeAPIService()


@pytest.fixture
def target_service():
    return FakeAPIService()


@pytest.fixture
def source_connector(source_service):
    return _make_source(source_service)


@pytest.fixture
def target_connector(target_service):
    return _make_target(target_service)


@pytest.fixture
def engine(source_connector, target_connector, sync_run_service, sync_state_service):
    return SyncEngine(
        source=source_connector,
        target=target_connector,
        sync_run_service=sync_run_service,
        sync_state_service=sync_state_service,
        tenant_id="tenant-1",
        logger=logging.getLogger("test.engine"),
    )


class TestSyncEngine:
    """Tests for the core sync engine."""

    async def test_basic_sync(self, engine, sync_state_service, target_service):
        """Sync all invoices from source to target."""
        for i in range(3):
            target_service.add_response(
                "POST",
                "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"},
                status=201,
            )

        run = await engine.run(
            source_credentials={"api_key": "***"},
            target_credentials={"api_key": "***"},
        )

        assert run.status == SyncRunStatus.COMPLETED
        assert run.records_processed == 3
        assert run.records_succeeded == 3
        assert run.records_failed == 0
        assert run.records_skipped == 0

    async def test_sync_idempotency(self, sync_run_service, sync_state_service):
        """Running sync twice should skip unchanged invoices."""
        # First run
        source_svc1 = FakeAPIService()
        target_svc1 = FakeAPIService()
        source1 = _make_source(source_svc1)
        target1 = _make_target(target_svc1)

        for i in range(3):
            target_svc1.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        engine1 = SyncEngine(
            source=source1, target=target1,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )

        creds = {"api_key": "***"}
        run1 = await engine1.run(source_credentials=creds, target_credentials=creds)
        assert run1.records_succeeded == 3

        # Second run — same source data, should skip all
        source_svc2 = FakeAPIService()
        target_svc2 = FakeAPIService()
        source2 = _make_source(source_svc2)
        target2 = _make_target(target_svc2)

        engine2 = SyncEngine(
            source=source2, target=target2,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )

        run2 = await engine2.run(source_credentials=creds, target_credentials=creds)
        assert run2.records_processed == 3
        assert run2.records_skipped == 3
        assert run2.records_succeeded == 0

    async def test_sync_detects_changes(self, sync_run_service, sync_state_service):
        """Modified invoices should be re-exported."""
        creds = {"api_key": "***"}

        # First run
        source_svc1 = FakeAPIService()
        target_svc1 = FakeAPIService()
        source1 = _make_source(source_svc1)
        target1 = _make_target(target_svc1)

        for i in range(3):
            target_svc1.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        engine1 = SyncEngine(
            source=source1, target=target1,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )
        run1 = await engine1.run(source_credentials=creds, target_credentials=creds)
        assert run1.records_succeeded == 3

        # Modify an invoice
        modified = [dict(inv) for inv in SAMPLE_INVOICES]
        modified[0]["amount"] = 999.99

        # Second run with modified data
        source_svc2 = FakeAPIService()
        target_svc2 = FakeAPIService()
        source2 = _make_source(source_svc2, invoices=modified)
        target2 = _make_target(target_svc2)

        # The changed invoice should trigger an update
        target_svc2.add_response(
            "POST", "/invoices/tgt-1",
            {"id": "tgt-1", "status": "updated"}, status=200,
        )

        engine2 = SyncEngine(
            source=source2, target=target2,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )
        run2 = await engine2.run(source_credentials=creds, target_credentials=creds)

        assert run2.records_processed == 3
        assert run2.records_skipped == 2
        assert run2.records_succeeded == 1

    async def test_state_tracking(self, engine, sync_state_service, target_service):
        """State store should track all records."""
        for i in range(3):
            target_service.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        creds = {"api_key": "***"}
        await engine.run(source_credentials=creds, target_credentials=creds)

        states = await sync_state_service.get_all_states("tenant-1")
        assert len(states) == 3

        for state in states:
            assert state.status == SyncStateStatus.EXPORTED
            assert state.target_record_id is not None
            assert state.content_hash != ""
            assert state.attempt_count >= 1

    async def test_state_counts(self, engine, sync_state_service, target_service):
        """State store should provide accurate counts."""
        for i in range(3):
            target_service.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        creds = {"api_key": "***"}
        await engine.run(source_credentials=creds, target_credentials=creds)

        counts = await sync_state_service.count_states("tenant-1")
        assert counts.get("exported") == 3

    async def test_tenant_isolation(self, sync_run_service, sync_state_service):
        """Different tenants should have isolated state."""
        creds = {"api_key": "***"}

        source_svc = FakeAPIService()
        target_svc = FakeAPIService()
        source = _make_source(source_svc)
        target = _make_target(target_svc)

        for i in range(3):
            target_svc.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        engine1 = SyncEngine(
            source=source, target=target,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )

        await engine1.run(source_credentials=creds, target_credentials=creds)

        assert len(await sync_state_service.get_all_states("tenant-2")) == 0
        assert len(await sync_state_service.get_all_states("tenant-1")) == 3

    async def test_failed_auth(self, sync_run_service, sync_state_service):
        """Sync should fail gracefully with bad credentials."""
        source_svc = FakeAPIService()
        source_svc.add_response(
            "POST", "/auth/token",
            {"detail": "Invalid API key"}, status=401,
        )
        source = SourceAPIConnector(APIClient(source_svc))

        target_svc = FakeAPIService()
        target_svc.add_response("POST", "/auth/token", AUTH_RESPONSE)
        target = TargetAPIConnector(APIClient(target_svc))

        engine = SyncEngine(
            source=source, target=target,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )

        run = await engine.run(
            source_credentials={"api_key": "***"},
            target_credentials={"api_key": "***"},
        )

        assert run.status == SyncRunStatus.FAILED
        assert run.records_processed == 0

    async def test_idempotency_key_format(self, engine):
        """Idempotency keys should follow tenant:source:id:hash format."""
        from src.models import UnifiedInvoice, CustomerRef, InvoiceStatus
        from datetime import date

        invoice = UnifiedInvoice(
            external_id="inv-001",
            invoice_number="INV-1001",
            customer=CustomerRef(external_id="cust-1"),
            currency="USD",
            total=Decimal("100"),
            tax_total=Decimal("20"),
            status=InvoiceStatus.SENT,
            issue_date=date(2024, 1, 15),
        )
        invoice = invoice.with_content_hash()

        key = engine._build_idempotency_key(invoice)
        parts = key.split(":")
        assert parts[0] == "tenant-1"
        assert parts[1] == "source_api"
        assert parts[2] == "inv-001"
        assert len(parts[3]) == 64  # SHA256 hex

    async def test_skip_stable_across_repeated_runs(
        self, sync_run_service, sync_state_service
    ):
        """Repeated runs must keep skipping unchanged invoices.

        Regression test: skipping used to set status=SKIPPED_UNCHANGED while
        the skip check required EXPORTED, so every second run re-exported.
        """
        creds = {"api_key": "***"}

        # Run 1: export all three invoices
        source_svc1 = FakeAPIService()
        target_svc1 = FakeAPIService()
        source1 = _make_source(source_svc1)
        target1 = _make_target(target_svc1)
        for i in range(3):
            target_svc1.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )
        engine1 = SyncEngine(
            source=source1, target=target1,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )
        run1 = await engine1.run(source_credentials=creds, target_credentials=creds)
        assert run1.records_succeeded == 3

        # Runs 2 and 3: no export responses queued on the target — any
        # export attempt would raise inside FakeAPIService and be recorded
        # as a failure, so clean skips prove no export was attempted.
        for _ in range(2):
            source_svc = FakeAPIService()
            target_svc = FakeAPIService()
            source = _make_source(source_svc)
            target = _make_target(target_svc)
            engine = SyncEngine(
                source=source, target=target,
                sync_run_service=sync_run_service,
                sync_state_service=sync_state_service,
                tenant_id="tenant-1",
                logger=logging.getLogger("test.engine"),
            )
            run = await engine.run(source_credentials=creds, target_credentials=creds)

            assert run.records_processed == 3
            assert run.records_skipped == 3
            assert run.records_succeeded == 0
            assert run.records_failed == 0
            export_calls = [
                r for r in target_svc.request_log if r["url"] == "/invoices"
            ]
            assert export_calls == []

    async def test_failed_invoice_preserves_target_link(
        self, sync_run_service, sync_state_service
    ):
        """A normalization failure must not wipe the target record link.

        Regression test: the error handler used to upsert a blank state,
        resetting target_record_id to None for previously exported invoices.
        """
        creds = {"api_key": "***"}

        # Run 1: export all three invoices
        source_svc1 = FakeAPIService()
        target_svc1 = FakeAPIService()
        source1 = _make_source(source_svc1)
        target1 = _make_target(target_svc1)
        for i in range(3):
            target_svc1.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )
        engine1 = SyncEngine(
            source=source1, target=target1,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )
        run1 = await engine1.run(source_credentials=creds, target_credentials=creds)
        assert run1.records_succeeded == 3

        # Run 2: inv-001 has a corrupt date → normalization fails
        broken = [dict(inv) for inv in SAMPLE_INVOICES]
        broken[0]["date"] = "not-a-date"

        source_svc2 = FakeAPIService()
        target_svc2 = FakeAPIService()
        source2 = _make_source(source_svc2, invoices=broken)
        target2 = _make_target(target_svc2)
        engine2 = SyncEngine(
            source=source2, target=target2,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )
        run2 = await engine2.run(source_credentials=creds, target_credentials=creds)

        assert run2.records_failed == 1
        assert run2.records_skipped == 2

        state = await sync_state_service.get_state("tenant-1", "source_api", "inv-001")
        assert state is not None
        assert state.status == SyncStateStatus.FAILED
        assert state.target_record_id == "tgt-1"  # link preserved
        assert state.last_error is not None

    async def test_replay_failed(self, sync_run_service, sync_state_service):
        """replay_failed reprocesses only FAILED states, re-fetching from source."""
        creds = {"api_key": "***"}

        # Run 1: two invoices export, one is rejected permanently (400)
        source_svc1 = FakeAPIService()
        target_svc1 = FakeAPIService()
        source1 = _make_source(source_svc1)
        target1 = _make_target(target_svc1)
        target_svc1.add_response(
            "POST", "/invoices", {"id": "tgt-1", "status": "created"}, status=201,
        )
        target_svc1.add_response(
            "POST", "/invoices", {"id": "tgt-2", "status": "created"}, status=201,
        )
        target_svc1.add_response(
            "POST", "/invoices", {"detail": "Validation failed"}, status=400,
        )
        engine1 = SyncEngine(
            source=source1, target=target1,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )
        run1 = await engine1.run(source_credentials=creds, target_credentials=creds)
        assert run1.records_succeeded == 2
        assert run1.records_failed == 1

        # Replay: source now serves the fixed invoice, target accepts it
        source_svc2 = FakeAPIService()
        source_svc2.add_response("POST", "/auth/token", AUTH_RESPONSE)
        source_svc2.add_response("GET", "/invoices/inv-003", SAMPLE_INVOICES[2])
        source2 = SourceAPIConnector(APIClient(source_svc2))

        target_svc2 = FakeAPIService()
        target_svc2.add_response("POST", "/auth/token", AUTH_RESPONSE)
        target_svc2.add_response(
            "POST", "/invoices", {"id": "tgt-3", "status": "created"}, status=201,
        )
        target2 = TargetAPIConnector(APIClient(target_svc2))

        engine2 = SyncEngine(
            source=source2, target=target2,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )
        replay_run = await engine2.replay_failed(
            source_credentials=creds, target_credentials=creds
        )

        assert replay_run.records_processed == 1
        assert replay_run.records_succeeded == 1
        assert replay_run.records_failed == 0

        state = await sync_state_service.get_state("tenant-1", "source_api", "inv-003")
        assert state is not None
        assert state.status == SyncStateStatus.EXPORTED
        assert state.target_record_id == "tgt-3"


class _FakeOutcome:
    def __init__(self, exc):
        self._exc = exc

    def exception(self):
        return self._exc


class _FakeRetryState:
    """Minimal stand-in for tenacity's RetryCallState."""

    def __init__(self, exc, attempt):
        self.outcome = _FakeOutcome(exc)
        self.attempt_number = attempt


class TestWaitStrategy:
    """Tests for the Retry-After-aware tenacity wait function."""

    def test_honors_server_retry_after(self):
        exc = RateLimitError("slow down", retry_after_seconds=7)
        assert wait_retry_after_aware(_FakeRetryState(exc, attempt=1)) == 7.0

    def test_retry_after_capped_at_60s(self):
        exc = RateLimitError("slow down", retry_after_seconds=3600)
        assert wait_retry_after_aware(_FakeRetryState(exc, attempt=1)) == 60.0

    def test_exponential_backoff_for_other_retryable_errors(self):
        result = ExportResult(
            status=Status.FAILED,
            error=SyncError(
                category=ErrorCategory.RETRYABLE,
                code="HTTP_500",
                message="boom",
            ),
        )
        exc = RetryableExportError(result)
        base = settings.retry_base_delay
        assert wait_retry_after_aware(_FakeRetryState(exc, attempt=1)) == base
        assert wait_retry_after_aware(_FakeRetryState(exc, attempt=3)) == base * 4


class TestSyncEngineWithPagination:
    """Tests for sync with paginated sources."""

    async def test_paginated_sync(self, sync_run_service, sync_state_service):
        """Sync should handle paginated source data."""
        invoices = [
            {
                "id": f"inv-{i:03d}",
                "number": f"INV-{1000 + i}",
                "customer": {"id": f"cust-{i}", "name": f"Customer {i}"},
                "amount": 100.0 + i,
                "tax": 20.0,
                "currency": "USD",
                "status": "sent",
                "date": "2024-01-15",
            }
            for i in range(25)
        ]

        # Source: 3 pages (10 + 10 + 5)
        source_svc = FakeAPIService()
        source_svc.add_response("POST", "/auth/token", AUTH_RESPONSE)
        source_svc.add_response(
            "GET", "/invoices",
            {"invoices": invoices[:10], "next_cursor": "10"},
        )
        source_svc.add_response(
            "GET", "/invoices",
            {"invoices": invoices[10:20], "next_cursor": "20"},
        )
        source_svc.add_response(
            "GET", "/invoices",
            {"invoices": invoices[20:], "next_cursor": None},
        )
        source = SourceAPIConnector(APIClient(source_svc))

        # Target: 25 export responses
        target_svc = FakeAPIService()
        target_svc.add_response("POST", "/auth/token", AUTH_RESPONSE)
        for i in range(25):
            target_svc.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )
        target = TargetAPIConnector(APIClient(target_svc))

        engine = SyncEngine(
            source=source, target=target,
            sync_run_service=sync_run_service,
            sync_state_service=sync_state_service,
            tenant_id="tenant-1",
            logger=logging.getLogger("test.engine"),
        )

        creds = {"api_key": "***"}
        run = await engine.run(
            source_credentials=creds,
            target_credentials=creds,
            batch_size=10,
        )

        assert run.status == SyncRunStatus.COMPLETED
        assert run.records_processed == 25
        assert run.records_succeeded == 25
