from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class RawInvoice(BaseModel):
    """
    Raw invoice data from source system, before normalization.
    Preserves original structure for debugging and replay.
    """

    source_id: str  # Source system's ID
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any]  # Original payload
