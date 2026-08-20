from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from src.abstract import SourceConnector
from src.abstract.exceptions import (
    AuthenticationError,
    NormalizationError,
    RateLimitError,
    SourceUnavailableError,
)
from src.clients.api_client import APIClient
from src.models import (
    Cursor,
    FetchResult,
    InvoiceStatus,
    RawInvoice,
    UnifiedInvoice,
)


class SourceAPIConnector(SourceConnector):
    """
    Source connector that fetches invoices from an HTTP invoicing API.

    Works with any APIService implementation:
    - AiohttpAPIService for real HTTP calls (integration/production)
    - FakeAPIService for unit tests (no HTTP overhead)

    Handles:
    - Token-based authentication
    - Cursor-based pagination
    - Rate limit detection (429 → RateLimitError)
    - Normalization from source format to UnifiedInvoice
    """

    def __init__(self, api_client: APIClient):
        self._client = api_client
        self._authenticated = False
        self._token: str | None = None

    @property
    def name(self) -> str:
        return "source_api"

    @property
    def supports_incremental(self) -> bool:
        return True

    async def authenticate(self, credentials: dict[str, Any]) -> None:
        """Authenticate with the source API via POST /auth/token."""
        body, status = await self._client.post(
            "/auth/token",
            body={"api_key": credentials.get("api_key", "")},
        )

        if status == 401:
            raise AuthenticationError("Invalid API key")
        if status != 200:
            raise SourceUnavailableError(f"Auth failed with status {status}")

        self._token = body.get("token")
        self._authenticated = True

    async def fetch_invoices(
        self,
        cursor: Cursor | None = None,
        limit: int = 100,
    ) -> FetchResult:
        """Fetch a page of invoices from GET /invoices."""
        if not self._authenticated:
            raise RuntimeError("Not authenticated")

        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor.value

        headers = {"Authorization": f"Bearer {self._token}"}

        body, status = await self._client.get(
            "/invoices",
            params=params,
            headers=headers,
        )

        if status == 429:
            retry_after = int(body.get("retry_after", 60))
            raise RateLimitError(
                "Rate limit exceeded",
                retry_after_seconds=retry_after,
            )
        if status == 401:
            raise AuthenticationError("Token expired or invalid")
        if status != 200:
            raise SourceUnavailableError(f"Fetch failed with status {status}")

        raw_invoices = [
            RawInvoice(
                source_id=str(inv.get("id", f"inv-{i}")),
                data=inv,
            )
            for i, inv in enumerate(body.get("invoices", []))
        ]

        next_cursor = None
        if body.get("next_cursor"):
            next_cursor = Cursor(value=body["next_cursor"])

        return FetchResult(
            invoices=raw_invoices,
            next_cursor=next_cursor,
        )

    def normalize(self, raw: RawInvoice) -> UnifiedInvoice:
        """
        Normalize source API invoice format to unified model.

        Source format:
        {
            "id": "inv-001",
            "number": "INV-1001",
            "customer": {"id": "cust-1", "name": "Acme Corp"} or "cust-1",
            "amount": 100.00,
            "tax": 20.00,
            "currency": "usd",
            "status": "sent",
            "date": "2024-01-15",
            "due": "2024-02-15",
            "updated": "2024-01-15T10:30:00Z",
            "lines": [{"desc": "...", "qty": 1, "price": 80, "total": 80, "tax_rate": 0.25}]
        }
        """
        data = raw.data

        try:
            # Parse customer — can be object or plain string ID
            customer_data = data.get("customer", {})
            if isinstance(customer_data, str):
                customer_data = {"id": customer_data}

            # Parse lines or synthesize from amount/tax
            lines = []
            if "lines" in data:
                for line_data in data["lines"]:
                    lines.append({
                        "description": line_data.get("desc", "Item"),
                        "quantity": Decimal(str(line_data.get("qty", 1))),
                        "unit_price": Decimal(str(line_data.get("price", 0))),
                        "total": Decimal(str(line_data.get("total", 0))),
                        "tax_rate": (
                            Decimal(str(line_data["tax_rate"]))
                            if line_data.get("tax_rate")
                            else None
                        ),
                    })
            else:
                amount = Decimal(str(data.get("amount", 0)))
                tax = Decimal(str(data.get("tax", 0)))
                lines.append({
                    "description": "Invoice total",
                    "quantity": Decimal("1"),
                    "unit_price": amount - tax,
                    "total": amount,
                    "tax_amount": tax,
                })

            # Map status string to enum
            status_map = {
                "draft": InvoiceStatus.DRAFT,
                "sent": InvoiceStatus.SENT,
                "paid": InvoiceStatus.PAID,
                "overdue": InvoiceStatus.OVERDUE,
                "void": InvoiceStatus.VOID,
                "credit": InvoiceStatus.CREDIT_NOTE,
            }
            status = status_map.get(
                data.get("status", "draft"), InvoiceStatus.DRAFT
            )

            # Parse dates
            issue_date = (
                date.fromisoformat(data["date"])
                if "date" in data
                else date.today()
            )
            due_date = (
                date.fromisoformat(data["due"]) if data.get("due") else None
            )

            # Parse updated timestamp
            source_updated_at = None
            if data.get("updated"):
                source_updated_at = datetime.fromisoformat(
                    data["updated"].replace("Z", "+00:00")
                )

            return UnifiedInvoice(
                external_id=str(data.get("id", raw.source_id)),
                invoice_number=str(data.get("number", raw.source_id)),
                customer={
                    "external_id": str(customer_data.get("id", "unknown")),
                    "name": customer_data.get("name"),
                    "email": customer_data.get("email"),
                    "raw": customer_data,
                },
                currency=data.get("currency", "USD"),
                total=Decimal(str(data.get("amount", 0))),
                tax_total=Decimal(str(data.get("tax", 0))),
                lines=lines,
                status=status,
                issue_date=issue_date,
                due_date=due_date,
                source_updated_at=source_updated_at,
                raw_payload=data,
            )

        except Exception as e:
            raise NormalizationError(
                f"Failed to normalize {raw.source_id}: {e}"
            ) from e
