"""Tests for SourceAPIConnector and TargetAPIConnector using FakeAPIService."""

import pytest
from decimal import Decimal

from src import (
    APIClient,
    AuthenticationError,
    ErrorCategory,
    FakeAPIService,
    NotSupportedError,
    RateLimitError,
    RecordNotFoundError,
    SourceAPIConnector,
    Status,
    TargetAPIConnector,
)
from src.models import ExportResult, InvoiceStatus, RawInvoice, UnifiedInvoice


# --- Sample data matching the fake source API format ---

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

AUTH_RESPONSE = {"token": "***", "expires_in": 3600}


def _setup_source_service(service: FakeAPIService, invoices=None):
    """Configure a FakeAPIService to act as a source API."""
    invoices = invoices or SAMPLE_INVOICES
    service.add_response("POST", "/auth/token", AUTH_RESPONSE)

    # Queue paginated invoice responses
    # First page: invoices[0:limit], cursor = str(limit)
    # We'll queue up pages dynamically in each test


def _setup_target_service(service: FakeAPIService):
    """Configure a FakeAPIService to act as a target API."""
    service.add_response("POST", "/auth/token", AUTH_RESPONSE)


class TestSourceAPIConnector:
    """Tests for SourceAPIConnector."""

    @pytest.fixture
    def service(self):
        return FakeAPIService()

    @pytest.fixture
    def client(self, service):
        return APIClient(service)

    @pytest.fixture
    def connector(self, client):
        return SourceAPIConnector(client)

    async def test_authenticate_success(self, connector, service):
        """Successful authentication."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})
        assert connector._authenticated

    async def test_authenticate_failure(self, connector, service):
        """Failed authentication returns 401."""
        service.add_response(
            "POST", "/auth/token", {"detail": "Invalid API key"}, status=401
        )
        with pytest.raises(AuthenticationError):
            await connector.authenticate({"api_key": "***"})

    async def test_fetch_invoices_first_page(self, connector, service):
        """Fetch first page of invoices."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "GET",
            "/invoices",
            {
                "invoices": SAMPLE_INVOICES[:2],
                "next_cursor": "2",
            },
        )

        result = await connector.fetch_invoices(limit=2)

        assert len(result.invoices) == 2
        assert result.next_cursor is not None
        assert result.next_cursor.value == "2"
        assert result.invoices[0].source_id == "inv-001"
        assert result.invoices[1].source_id == "inv-002"

    async def test_fetch_invoices_pagination(self, connector, service):
        """Fetch all invoices across pages."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        # Page 1
        service.add_response(
            "GET",
            "/invoices",
            {"invoices": SAMPLE_INVOICES[:2], "next_cursor": "2"},
        )
        # Page 2 (last)
        service.add_response(
            "GET",
            "/invoices",
            {"invoices": SAMPLE_INVOICES[2:], "next_cursor": None},
        )

        all_invoices = []
        cursor = None
        while True:
            result = await connector.fetch_invoices(cursor=cursor, limit=2)
            all_invoices.extend(result.invoices)
            if result.next_cursor is None:
                break
            cursor = result.next_cursor

        assert len(all_invoices) == 3

    async def test_fetch_all_invoices_iterator(self, connector, service):
        """Fetch all invoices using async iterator."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "GET",
            "/invoices",
            {"invoices": SAMPLE_INVOICES[:2], "next_cursor": "2"},
        )
        service.add_response(
            "GET",
            "/invoices",
            {"invoices": SAMPLE_INVOICES[2:], "next_cursor": None},
        )

        invoices = []
        async for invoice in connector.fetch_all_invoices(batch_size=2):
            invoices.append(invoice)

        assert len(invoices) == 3

    async def test_fetch_invoice_by_id_not_found(self, connector, service):
        """404 on single-invoice fetch is a permanent RecordNotFoundError."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "GET",
            "/invoices/inv-999",
            {"detail": "Not found"},
            status=404,
        )

        with pytest.raises(RecordNotFoundError):
            await connector.fetch_invoice_by_id("inv-999")

    async def test_rate_limit(self, connector, service):
        """Rate limiting returns 429."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "GET",
            "/invoices",
            {"detail": "Rate limited", "retry_after": 60},
            status=429,
        )

        with pytest.raises(RateLimitError) as exc_info:
            await connector.fetch_invoices(limit=1)

        assert exc_info.value.retry_after_seconds == 60

    def test_normalize(self, connector):
        """Normalize raw invoice to unified model."""
        raw_invoice = RawInvoice(source_id="inv-001", data=SAMPLE_INVOICES[0])
        unified = connector.normalize(raw_invoice)

        assert unified.external_id == "inv-001"
        assert unified.invoice_number == "INV-1001"
        assert unified.customer.external_id == "cust-1"
        assert unified.customer.name == "Acme Corp"
        assert unified.total == Decimal("100.0")
        assert unified.tax_total == Decimal("20.0")
        assert unified.currency == "USD"
        assert unified.status == InvoiceStatus.SENT

    def test_normalize_string_customer(self, connector):
        """Normalize invoice with string customer ID."""
        raw_invoice = RawInvoice(source_id="inv-003", data=SAMPLE_INVOICES[2])
        unified = connector.normalize(raw_invoice)

        assert unified.customer.external_id == "cust-3"
        assert unified.customer.name is None

    def test_normalize_with_lines(self, connector):
        """Normalize invoice with line items."""
        data = {
            "id": "inv-100",
            "number": "INV-1100",
            "customer": {"id": "cust-1"},
            "amount": 200.00,
            "tax": 40.00,
            "currency": "USD",
            "status": "sent",
            "date": "2024-01-20",
            "lines": [
                {"desc": "Consulting", "qty": 10, "price": 16.00, "total": 160.00, "tax_rate": 0.25},
                {"desc": "Support", "qty": 1, "price": 40.00, "total": 40.00},
            ],
        }
        raw_invoice = RawInvoice(source_id="inv-100", data=data)
        unified = connector.normalize(raw_invoice)

        assert len(unified.lines) == 2
        assert unified.lines[0].description == "Consulting"
        assert unified.lines[0].quantity == Decimal("10")
        assert unified.lines[1].description == "Support"


class TestTargetAPIConnector:
    """Tests for TargetAPIConnector."""

    @pytest.fixture
    def service(self):
        return FakeAPIService()

    @pytest.fixture
    def client(self, service):
        return APIClient(service)

    @pytest.fixture
    def connector(self, client):
        return TargetAPIConnector(client)

    @pytest.fixture
    def sample_invoice(self):
        return UnifiedInvoice(
            external_id="inv-001",
            invoice_number="INV-1001",
            customer={"external_id": "cust-1", "name": "Acme"},
            currency="USD",
            total=Decimal("100.00"),
            tax_total=Decimal("20.00"),
            status=InvoiceStatus.SENT,
            issue_date="2024-01-15",
        )

    async def test_authenticate_success(self, connector, service):
        """Successful authentication."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})
        assert connector._authenticated

    async def test_authenticate_failure(self, connector, service):
        """Failed authentication."""
        service.add_response(
            "POST", "/auth/token", {"detail": "Invalid"}, status=401
        )
        with pytest.raises(AuthenticationError):
            await connector.authenticate({"api_key": "***"})

    async def test_export_invoice_create(self, connector, service, sample_invoice):
        """Export new invoice returns 201."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices",
            {"id": "tgt-1", "status": "created"},
            status=201,
        )

        result = await connector.export_invoice(sample_invoice, "idem-key-123")

        assert result.is_success
        assert result.status == Status.CREATED
        assert result.target_id == "tgt-1"
        assert result.idempotency_key == "idem-key-123"

    async def test_export_invoice_idempotent_replay(self, connector, service, sample_invoice):
        """Same idempotency key returns 200 with existing record."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices",
            {"id": "tgt-1", "status": "exists"},
            status=200,
        )

        result = await connector.export_invoice(sample_invoice, "idem-key-123")

        assert result.is_success
        assert result.status == Status.ALREADY_EXISTS
        assert result.target_id == "tgt-1"

    async def test_export_invoice_duplicate_conflict(self, connector, service, sample_invoice):
        """Duplicate invoice number returns 409."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices",
            {
                "detail": "Invoice number INV-1001 already exists",
                "existing_id": "tgt-99",
            },
            status=409,
        )

        result = await connector.export_invoice(sample_invoice, "new-key")

        assert not result.is_success
        assert result.status == Status.ALREADY_EXISTS
        assert result.error is not None
        assert result.error.code == "DUPLICATE_INVOICE_NUMBER"

    async def test_export_rate_limit(self, connector, service, sample_invoice):
        """Rate limited export raises RateLimitError."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices",
            {"detail": "Rate limited", "retry_after": 30},
            status=429,
        )

        with pytest.raises(RateLimitError) as exc_info:
            await connector.export_invoice(sample_invoice, "key-1")

        assert exc_info.value.retry_after_seconds == 30

    async def test_update_invoice(self, connector, service, sample_invoice):
        """Update existing invoice."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices/tgt-1",
            {"id": "tgt-1", "status": "updated"},
            status=200,
        )

        result = await connector.update_invoice("tgt-1", sample_invoice)

        assert result.is_success
        assert result.status == Status.UPDATED
        assert result.target_id == "tgt-1"

    async def test_update_not_found(self, connector, service, sample_invoice):
        """Update non-existent invoice returns 404."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices/tgt-999",
            {"detail": "Not found"},
            status=404,
        )

        result = await connector.update_invoice("tgt-999", sample_invoice)

        assert not result.is_success
        assert result.error is not None
        assert result.error.code == "NOT_FOUND"

    async def test_export_server_error_is_retryable(self, connector, service, sample_invoice):
        """5xx from target is classified as retryable, not permanent."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices",
            {"detail": "Internal server error"},
            status=500,
        )

        result = await connector.export_invoice(sample_invoice, "key-1")

        assert not result.is_success
        assert result.status == Status.FAILED
        assert result.error is not None
        assert result.error.category == ErrorCategory.RETRYABLE
        assert result.is_retryable

    async def test_export_client_error_is_permanent(self, connector, service, sample_invoice):
        """4xx (non-409/429) from target is classified as permanent."""
        service.add_response("POST", "/auth/token", AUTH_RESPONSE)
        await connector.authenticate({"api_key": "***"})

        service.add_response(
            "POST",
            "/invoices",
            {"detail": "Validation failed"},
            status=400,
        )

        result = await connector.export_invoice(sample_invoice, "key-1")

        assert not result.is_success
        assert result.status == Status.FAILED
        assert result.error is not None
        assert result.error.category == ErrorCategory.PERMANENT
        assert not result.is_retryable
