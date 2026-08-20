from dataclasses import dataclass
from typing import Any

from src.services import APIService


@dataclass
class APIClient:
    """
    HTTP client that delegates to an APIService.

    The client is a thin wrapper — it doesn't know about sessions,
    connection pooling, or HTTP libraries. Swap the service
    implementation without changing client code.
    """

    service: APIService

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Make GET request. Returns (response_body, status_code)."""
        return await self.service.get(url, params=params, headers=headers)

    async def post(
        self,
        url: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Make POST request. Returns (response_body, status_code)."""
        return await self.service.post(url, body=body, headers=headers)
