from decimal import Decimal

from pydantic import BaseModel


class InvoiceLine(BaseModel):
    """Single line item on an invoice."""

    description: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal
    total: Decimal
    tax_rate: Decimal | None = None  # Percentage, e.g., 20.0 for 20%
    tax_amount: Decimal | None = None
    account_code: str | None = None  # GL account code if provided by source
