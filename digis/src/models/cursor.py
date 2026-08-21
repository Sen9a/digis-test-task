from typing import Any

from pydantic import BaseModel, Field


class Cursor(BaseModel):
    """
    Pagination cursor — opaque to the platform, meaningful to the connector.

    Examples:
    - "2024-01-15T10:30:00Z" (timestamp-based)
    - "eyJpZCI6MTAwfQ==" (base64 encoded ID)
    - "page:5" (page number)
    """

    value: str
    connector_metadata: dict[str, Any] = Field(default_factory=dict)
