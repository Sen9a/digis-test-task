import pytest
from decimal import Decimal
from datetime import date

from src import SourceConnector, TargetConnector
from src.const import ErrorCategory
from src.models import ExportResult, InvoiceStatus, SyncError, UnifiedInvoice


class TestConnectorInterfaces:
    """Verify connector abstract are well-defined."""

    def test_source_connector_is_abstract(self):
        """Cannot instantiate SourceConnector directly."""
        with pytest.raises(TypeError):
            SourceConnector()

    def test_target_connector_is_abstract(self):
        """Cannot instantiate TargetConnector directly."""
        with pytest.raises(TypeError):
            TargetConnector()

    def test_unified_invoice_content_hash(self):
        """Content hash is deterministic and changes with data."""
        invoice1 = UnifiedInvoice(
            external_id="INV-001",
            invoice_number="1001",
            customer={"external_id": "CUST-1", "name": "Acme"},
            currency="USD",
            total=Decimal("100.00"),
            tax_total=Decimal("20.00"),
            status=InvoiceStatus.SENT,
            issue_date=date(2024, 1, 15),
        ).with_content_hash()

        invoice2 = UnifiedInvoice(
            external_id="INV-001",
            invoice_number="1001",
            customer={"external_id": "CUST-1", "name": "Acme"},
            currency="USD",
            total=Decimal("100.00"),
            tax_total=Decimal("20.00"),
            status=InvoiceStatus.SENT,
            issue_date=date(2024, 1, 15),
        ).with_content_hash()

        # Same data = same hash
        assert invoice1.content_hash == invoice2.content_hash

        # Different data = different hash
        invoice2.total = Decimal("200.00")
        invoice2.content_hash = invoice2.compute_content_hash()
        assert invoice1.content_hash != invoice2.content_hash

    def test_export_result_success(self):
        """ExportResult correctly identifies success."""
        result = ExportResult(
            status=ExportResult.Status.CREATED,
            target_id="target-123",
        )
        assert result.is_success
        assert not result.is_retryable

    def test_export_result_retryable(self):
        """ExportResult correctly identifies retryable errors."""
        from src.const import ErrorCategory
        from src.models import SyncError

        result = ExportResult(
            status=ExportResult.Status.CREATED,
            error=SyncError(
                category=ErrorCategory.RETRYABLE,
                code="RATE_LIMITED",
                message="Too many requests",
                retry_after_seconds=60,
            ),
        )
        assert not result.is_success
        assert result.is_retryable
