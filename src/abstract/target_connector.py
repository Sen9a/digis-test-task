from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.models import ExportResult, UnifiedInvoice


class TargetConnector(ABC):
    """
    Exports unified invoices to a target accounting system.

    Responsibilities:
    - Authenticate with target API
    - Declare capabilities (idempotency, update, reversal)
    - Export invoices using appropriate strategy
    - Handle target-specific rate limits

    Does NOT:
    - Generate idempotency keys (platform provides)
    - Know about sync state or other tenants
    - Decide between create/update/reverse (platform routes based on capabilities)
    """

    # --- Capabilities (override in subclass) ---

    @property
    @abstractmethod
    def name(self) -> str:
        """Connector identifier, e.g., 'quickbooks', 'xero', 'netsuite'."""
        ...

    @property
    def supports_idempotency_keys(self) -> bool:
        """Whether target accepts idempotency keys for safe retries."""
        return False

    @property
    def supports_update(self) -> bool:
        """Whether existing entries can be updated in place."""
        return False

    @property
    def requires_reversal(self) -> bool:
        """Whether changes require reversing old entry and creating new one."""
        return False

    @property
    def rejects_duplicates(self) -> bool:
        """Whether target rejects duplicate invoice numbers."""
        return False

    @property
    def is_async(self) -> bool:
        """Whether target responds asynchronously (webhook/polling)."""
        return False

    # --- Authentication ---

    @abstractmethod
    async def authenticate(self, credentials: dict[str, Any]) -> None:
        """Validate credentials and establish connection."""
        ...

    # --- Export Operations ---

    @abstractmethod
    async def export_invoice(
        self,
        invoice: UnifiedInvoice,
        idempotency_key: str,
    ) -> ExportResult:
        """
        Export invoice to target system.

        Args:
            invoice: Unified invoice to export
            idempotency_key: Platform-generated key for safe retries

        Returns:
            ExportResult with status and target ID

        Note:
            If target rejects duplicates and invoice already exists,
            should return ALREADY_EXISTS status, not raise.
        """
        ...

    async def update_invoice(
        self,
        target_id: str,
        invoice: UnifiedInvoice,
    ) -> ExportResult:
        """
        Update existing invoice in target.

        Only called if supports_update is True.

        Raises:
            NotSupportedError: If updates not supported
        """
        if not self.supports_update:
            raise NotSupportedError(f"{self.name} does not support updates")
        raise NotImplementedError

    async def reverse_invoice(
        self,
        target_id: str,
        reason: str,
    ) -> ExportResult:
        """
        Reverse (void/credit) existing invoice.

        Only called if requires_reversal is True.

        Raises:
            NotSupportedError: If reversal not supported
        """
        if not self.requires_reversal:
            raise NotSupportedError(f"{self.name} does not require reversal")
        raise NotImplementedError
