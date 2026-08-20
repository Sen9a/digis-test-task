from __future__ import annotations

from typing import Any

from src.abstract import TargetConnector
from exceptions import (
    AuthenticationError,
    RateLimitError,
)
from src.clients.api_client import APIClient
from src.const import ErrorCategory
from src.models import ExportResult, SyncError, UnifiedInvoice


class TargetAPIConnector(TargetConnector):
    """
    Target connector that exports invoices to an HTTP accounting API.

    Works with any APIService implementation:
    - AiohttpAPIService for real HTTP calls (integration/production)
    - FakeAPIService for unit tests (no HTTP overhead)

    Handles:
    - Token-based authentication
    - Idempotency-Key header for safe retries
    - Duplicate detection (409 Conflict)
    - Rate limit detection (429 → RateLimitError)
    """

    def __init__(self, api_client: APIClient):
        self._client = api_client
        self._authenticated = False
        self._token: str | None = None

    @property
    def name(self) -> str:
        return "target_api"

    @property
    def supports_idempotency_keys(self) -> bool:
        return True

    @property
    def supports_update(self) -> bool:
        return True

    @property
    def rejects_duplicates(self) -> bool:
        return True

    async def authenticate(self, credentials: dict[str, Any]) -> None:
        """Authenticate with the target API via POST /auth/token."""
        body, status = await self._client.post(
            "/auth/token",
            body={"api_key": credentials.get("api_key", "")},
        )

        if status == 401:
            raise AuthenticationError("Invalid API key")
        if status != 200:
            raise AuthenticationError(f"Auth failed with status {status}")

        self._token = body.get("token")
        self._authenticated = True

    async def export_invoice(
        self,
        invoice: UnifiedInvoice,
        idempotency_key: str,
    ) -> ExportResult:
        """Export invoice to target via POST /invoices."""
        if not self._authenticated:
            raise RuntimeError("Not authenticated")

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Idempotency-Key": idempotency_key,
        }

        payload = self._to_target_payload(invoice)

        body, status = await self._client.post(
            "/invoices",
            body=payload,
            headers=headers,
        )

        if status == 429:
            retry_after = int(body.get("retry_after", 60))
            raise RateLimitError(
                "Rate limit exceeded",
                retry_after_seconds=retry_after,
            )

        if status == 409:
            return ExportResult(
                status=ExportResult.Status.ALREADY_EXISTS,
                target_id=body.get("existing_id"),
                idempotency_key=idempotency_key,
                error=SyncError(
                    category=ErrorCategory.CONFLICT,
                    code="DUPLICATE_INVOICE_NUMBER",
                    message=body.get("detail", "Duplicate invoice number"),
                    details=body,
                ),
            )

        if status == 200:
            # Idempotent replay — already exists
            return ExportResult(
                status=ExportResult.Status.ALREADY_EXISTS,
                target_id=body.get("id"),
                idempotency_key=idempotency_key,
                response_data=body,
            )

        if status == 201:
            return ExportResult(
                status=ExportResult.Status.CREATED,
                target_id=body.get("id"),
                idempotency_key=idempotency_key,
                response_data=body,
            )

        # 5xx and other server-side failures are retryable; 4xx are permanent
        category = (
            ErrorCategory.RETRYABLE if status >= 500 else ErrorCategory.PERMANENT
        )
        return ExportResult(
            status=ExportResult.Status.FAILED,
            error=SyncError(
                category=category,
                code=f"HTTP_{status}",
                message=f"Unexpected status {status}: {body}",
                details=body,
            ),
        )

    async def update_invoice(
        self,
        target_id: str,
        invoice: UnifiedInvoice,
    ) -> ExportResult:
        """Update existing invoice via POST /invoices/{id}."""
        if not self._authenticated:
            raise RuntimeError("Not authenticated")

        headers = {"Authorization": f"Bearer {self._token}"}
        payload = self._to_target_payload(invoice)

        body, status = await self._client.post(
            f"/invoices/{target_id}",
            body=payload,
            headers=headers,
        )

        if status == 404:
            return ExportResult(
                status=ExportResult.Status.FAILED,
                error=SyncError(
                    category=ErrorCategory.PERMANENT,
                    code="NOT_FOUND",
                    message=f"Invoice {target_id} not found",
                ),
            )

        if status == 200:
            return ExportResult(
                status=ExportResult.Status.UPDATED,
                target_id=target_id,
                response_data=body,
            )

        category = (
            ErrorCategory.RETRYABLE if status >= 500 else ErrorCategory.PERMANENT
        )
        return ExportResult(
            status=ExportResult.Status.FAILED,
            error=SyncError(
                category=category,
                code=f"HTTP_{status}",
                message=f"Update failed with status {status}",
                details=body,
            ),
        )

    def _to_target_payload(self, invoice: UnifiedInvoice) -> dict[str, Any]:
        """Convert unified invoice to target API payload format."""
        return {
            "external_id": invoice.external_id,
            "invoice_number": invoice.invoice_number,
            "customer_id": invoice.customer.external_id,
            "customer_name": invoice.customer.name,
            "currency": invoice.currency,
            "total": str(invoice.total),
            "tax_total": str(invoice.tax_total),
            "status": invoice.status.value,
            "issue_date": invoice.issue_date.isoformat(),
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "lines": [
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "total": str(line.total),
                    "tax_rate": str(line.tax_rate) if line.tax_rate else None,
                }
                for line in invoice.lines
            ],
        }
