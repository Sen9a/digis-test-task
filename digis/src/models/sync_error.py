from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from src.const import ErrorCategory


class SyncError(BaseModel):
    """Structured error information for traceability and replay decisions."""

    category: ErrorCategory
    code: str  # e.g., "RATE_LIMITED", "VALIDATION_FAILED", "DUPLICATE_INVOICE"
    message: str
    retry_after_seconds: int | None = None  # For rate limits
    details: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
