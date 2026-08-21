from typing import Any

from pydantic import BaseModel, Field

from src.const import ErrorCategory, Status
from src.models.sync_error import SyncError


class ExportResult(BaseModel):
    """
    Result of exporting an invoice to a target system.

    Success doesn't always mean "created" — could be "updated", "skipped",
    or "already existed".
    """

    status: Status
    target_id: str | None = None  # Target system's record ID
    idempotency_key: str | None = None  # Key used for idempotency
    response_data: dict[str, Any] = Field(default_factory=dict)  # Target response
    error: SyncError | None = None  # Set if status indicates failure

    @property
    def is_success(self) -> bool:
        return self.error is None

    @property
    def is_retryable(self) -> bool:
        return self.error is not None and self.error.category == ErrorCategory.RETRYABLE
