from abc import ABC, abstractmethod
from typing import Any


class APIService(ABC):
    """
    Abstract API service that handles HTTP communication.

    Manages its own connection lifecycle (sessions, pools, etc.).
    Implementations can use aiohttp, httpx, requests, etc.
    """

    @abstractmethod
    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Make GET request. Returns (response_body, status_code)."""
        ...

    @abstractmethod
    async def post(
        self,
        url: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Make POST request. Returns (response_body, status_code)."""
        ...
