from pydantic import BaseModel

from src.models.cursor import Cursor
from src.models.raw_invoice import RawInvoice


class FetchResult(BaseModel):
    """Result of fetching a batch of invoices from source."""

    invoices: list[RawInvoice]
    next_cursor: Cursor | None = None  # None = no more pages
    rate_limited: bool = False
    retry_after_seconds: int | None = None
