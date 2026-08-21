from typing import Any

from pydantic import BaseModel, Field


class CustomerRef(BaseModel):
    """Normalized customer reference."""

    external_id: str  # Source system's customer ID
    name: str | None = None
    email: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)  # Original source data
