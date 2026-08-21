from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator

from src.models.customer_ref import CustomerRef
from src.models.invoice_line import InvoiceLine
from src.models.invoice_status import InvoiceStatus


class UnifiedInvoice(BaseModel):
    """
    Canonical invoice model — superset of all source/target representations.

    This is the contract between source connectors (produce) and target
    connectors (consume).
    """

    # Identity
    external_id: str  # Source system's unique ID
    invoice_number: str  # Human-readable number

    # Parties
    customer: CustomerRef

    # Financials
    currency: str  # ISO 4217, e.g., "USD", "EUR"
    total: Decimal  # Total including tax
    tax_total: Decimal
    subtotal: Decimal | None = None  # Total excluding tax
    lines: list[InvoiceLine] = Field(default_factory=list)

    # Lifecycle
    status: InvoiceStatus
    issue_date: date
    due_date: date | None = None

    # Metadata
    source_updated_at: datetime | None = None  # None if source doesn't track
    raw_payload: dict[str, Any] = Field(default_factory=dict)  # Original source data

    # Computed (set by platform, not connector)
    content_hash: str | None = None  # SHA256 of semantically significant fields

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    def compute_content_hash(self) -> str:
        """
        Compute hash of semantically significant fields.
        Used for change detection and idempotency.
        """
        significant = {
            "external_id": self.external_id,
            "invoice_number": self.invoice_number,
            "customer_id": self.customer.external_id,
            "currency": self.currency,
            "total": str(self.total),
            "tax_total": str(self.tax_total),
            "status": self.status.value,
            "issue_date": self.issue_date.isoformat(),
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "lines": [
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "total": str(line.total),
                    "tax_rate": str(line.tax_rate) if line.tax_rate else None,
                }
                for line in self.lines
            ],
        }
        content = json.dumps(significant, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    def with_content_hash(self) -> Self:
        """Return copy with content_hash computed."""
        self.content_hash = self.compute_content_hash()
        return self
