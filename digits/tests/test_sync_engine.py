"""Tests for the sync engine."""

from decimal import Decimal

import pytest

from src.connectors.source import SourceAPIConnector
from src.connectors.target import TargetAPIConnector
from src.models import (
    ExportResult,
    SyncStateStatus,
    SyncRunStatus,
)
from src.services.fake_service import FakeAPIService
from src.clients.api_client import APIClient
from src.sync.engine import SyncEngine
from src.sync.state import StateStore

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

    # Queue all invoices in one page (tests can override for pagination)
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

    # Track created invoices for idempotency
    created: dict[str, dict] = {}
    idempotency_map: dict[str, str] = {}
    counter = {"next_id": 1}

    # We need to queue responses dynamically, so we'll use a custom approach
    # For the basic case, queue a 201 for each expected export
    # Tests that need specific behavior should configure the service directly

    client = APIClient(service)
    return TargetAPIConnector(client), created, idempotency_map, counter


@pytest.fixture
def state_store():
    return StateStore()


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
    connector, _, _, _ = _make_target(target_service)
    return connector


@pytest.fixture
def engine(source_connector, target_connector, state_store):
    return SyncEngine(
        source=source_connector,
        target=target_connector,
        store=state_store,
        tenant_id="tenant-1",
    )


class TestSyncEngine:
    """Tests for the core sync engine."""

    async def test_basic_sync(self, engine, state_store, target_service):
        """Sync all invoices from source to target."""
        # Queue target export responses (201 for each invoice)
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

    async def test_sync_idempotency(self, state_store):
        """Running sync twice should skip unchanged invoices."""
        # First run
        source_svc1 = FakeAPIService()
        target_svc1 = FakeAPIService()
        source1 = _make_source(source_svc1)
        target1, _, _, _ = _make_target(target_svc1)

        for i in range(3):
            target_svc1.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        engine1 = SyncEngine(
            source=source1, target=target1,
            store=state_store, tenant_id="tenant-1",
        )

        creds = {"api_key": "***"}
        run1 = await engine1.run(source_credentials=creds, target_credentials=creds)
        assert run1.records_succeeded == 3

        # Second run — same source data, should skip all
        source_svc2 = FakeAPIService()
        target_svc2 = FakeAPIService()
        source2 = _make_source(source_svc2)
        target2, _, _, _ = _make_target(target_svc2)

        engine2 = SyncEngine(
            source=source2, target=target2,
            store=state_store, tenant_id="tenant-1",
        )

        run2 = await engine2.run(source_credentials=creds, target_credentials=creds)
        assert run2.records_processed == 3
        assert run2.records_skipped == 3
        assert run2.records_succeeded == 0

    async def test_sync_detects_changes(self, state_store):
        """Modified invoices should be re-exported."""
        creds = {"api_key": "***"}

        # First run
        source_svc1 = FakeAPIService()
        target_svc1 = FakeAPIService()
        source1 = _make_source(source_svc1)
        target1, _, _, _ = _make_target(target_svc1)

        for i in range(3):
            target_svc1.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        engine1 = SyncEngine(
            source=source1, target=target1,
            store=state_store, tenant_id="tenant-1",
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
        target2, _, _, _ = _make_target(target_svc2)

        # The changed invoice should trigger an update (target supports update)
        target_svc2.add_response(
            "POST", "/invoices/tgt-1",
            {"id": "tgt-1", "status": "updated"}, status=200,
        )

        engine2 = SyncEngine(
            source=source2, target=target2,
            store=state_store, tenant_id="tenant-1",
        )
        run2 = await engine2.run(source_credentials=creds, target_credentials=creds)

        assert run2.records_processed == 3
        assert run2.records_skipped == 2
        assert run2.records_succeeded == 1

    async def test_state_tracking(self, engine, state_store, target_service):
        """State store should track all records."""
        for i in range(3):
            target_service.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        creds = {"api_key": "***"}
        await engine.run(source_credentials=creds, target_credentials=creds)

        states = await state_store.get_all_states("tenant-1")
        assert len(states) == 3

        for state in states:
            assert state.status == SyncStateStatus.EXPORTED
            assert state.target_record_id is not None
            assert state.content_hash != ""
            assert state.attempt_count >= 1

    async def test_state_counts(self, engine, state_store, target_service):
        """State store should provide accurate counts."""
        for i in range(3):
            target_service.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        creds = {"api_key": "***"}
        await engine.run(source_credentials=creds, target_credentials=creds)

        counts = await state_store.count_states("tenant-1")
        assert counts.get("exported") == 3

    async def test_tenant_isolation(self, state_store):
        """Different tenants should have isolated state."""
        creds = {"api_key": "***"}

        source_svc = FakeAPIService()
        target_svc = FakeAPIService()
        source = _make_source(source_svc)
        target, _, _, _ = _make_target(target_svc)

        for i in range(3):
            target_svc.add_response(
                "POST", "/invoices",
                {"id": f"tgt-{i+1}", "status": "created"}, status=201,
            )

        store1 = StateStore()
        store2 = StateStore()

        engine1 = SyncEngine(
            source=source, target=target,
            store=store1, tenant_id="tenant-1",
        )

        await engine1.run(source_credentials=creds, target_credentials=creds)

        assert len(await store2.get_all_states("tenant-2")) == 0
        assert len(await store1.get_all_states("tenant-1")) == 3

    async def test_failed_auth(self, state_store):
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
            store=state_store, tenant_id="tenant-1",
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


class TestSyncEngineWithPagination:
    """Tests for sync with paginated sources."""

    async def test_paginated_sync(self, state_store):
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
            store=state_store, tenant_id="tenant-1",
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
