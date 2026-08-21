from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from exceptions import NormalizationError
from src.models import Cursor, FetchResult, RawInvoice, UnifiedInvoice


class SourceConnector(ABC):
    """
    Fetches invoices from a source invoicing system.

    Responsibilities:
    - Authenticate with source API
    - Handle pagination (cursor or page-based)
    - Normalize source-specific format to UnifiedInvoice
    - Handle source-specific rate limits

    Does NOT:
    - Know about sync state or other tenants
    - Handle target-specific logic
    - Manage retries (platform orchestrates)
    """

    # --- Capabilities (override in subclass) ---

    @property
    @abstractmethod
    def name(self) -> str:
        """Connector identifier, e.g., 'quickbooks', 'xero', 'stripe'."""
        ...

    @property
    def supports_incremental(self) -> bool:
        """Whether source provides modification tracking (updated_at, cursor)."""
        return False

    @property
    def supports_webhooks(self) -> bool:
        """Whether source can push changes via webhook."""
        return False

    # --- Authentication ---

    @abstractmethod
    async def authenticate(self, credentials: dict[str, Any]) -> None:
        """
        Validate credentials and establish connection.

        Raises:
            AuthenticationError: If credentials are invalid
        """
        ...

    # --- Fetching ---

    @abstractmethod
    async def fetch_invoice_by_id(self, invoice_id: str) -> RawInvoice:
        """
        Fetch a single invoice by its source ID.

        Args:
            invoice_id: The source system's invoice ID

        Returns:
            RawInvoice with the source data

        Raises:
            SourceUnavailableError: If source is temporarily down
            AuthenticationError: If token is expired
        """
        ...

    @abstractmethod
    async def fetch_invoices(
        self,
        cursor: Cursor | None = None,
        limit: int = 100,
    ) -> FetchResult:
        """
        Fetch a batch of invoices from source.

        Args:
            cursor: Pagination cursor from previous fetch, None for first page
            limit: Max records to return (source may return fewer)

        Returns:
            FetchResult with invoices and next cursor

        Raises:
            RateLimitError: If rate limited (includes retry_after)
            SourceUnavailableError: If source is temporarily down
        """
        ...

    async def fetch_all_invoices(
        self,
        cursor: Cursor | None = None,
        batch_size: int = 100,
    ) -> AsyncIterator[RawInvoice]:
        """
        Convenience method to iterate through all invoices.

        Yields invoices one at a time, handling pagination internally.
        """
        current_cursor = cursor
        while True:
            result = await self.fetch_invoices(cursor=current_cursor, limit=batch_size)
            for invoice in result.invoices:
                yield invoice
            if result.next_cursor is None:
                break
            current_cursor = result.next_cursor

    # --- Normalization ---

    @abstractmethod
    def normalize(self, raw: RawInvoice) -> UnifiedInvoice:
        """
        Convert source-specific raw data to unified model.

        Must be pure function — no side effects, no I/O.

        Raises:
            NormalizationError: If data cannot be normalized
        """
        ...

    def normalize_batch(self, raws: list[RawInvoice]) -> list[UnifiedInvoice]:
        """Normalize multiple invoices, collecting errors."""
        results = []
        for raw in raws:
            try:
                results.append(self.normalize(raw))
            except Exception as e:
                # Platform will handle per-record errors
                raise NormalizationError(f"Failed to normalize {raw.source_id}: {e}") from e
        return results
