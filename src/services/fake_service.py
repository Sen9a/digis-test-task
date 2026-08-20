from typing import Any

from src.services.base import APIService


class FakeAPIService(APIService):
    """
    Fake API service for testing.

    Records all requests and returns configurable responses.
    """

    def __init__(self):
        self._responses: dict[tuple[str, str], list[tuple[dict[str, Any], int]]] = {}
        self._request_log: list[dict[str, Any]] = []

    def add_response(
        self,
        method: str,
        url: str,
        body: dict[str, Any],
        status: int = 200,
    ) -> None:
        """Queue a response for a method+URL combination."""
        key = (method.upper(), url)
        if key not in self._responses:
            self._responses[key] = []
        self._responses[key].append((body, status))

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        return await self._handle("GET", url, params=params, headers=headers)

    async def post(
        self,
        url: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        return await self._handle("POST", url, body=body, headers=headers)

    async def _handle(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        self._request_log.append({
            "method": method,
            "url": url,
            "params": params,
            "body": body,
            "headers": headers,
        })

        key = (method, url)
        if key not in self._responses or not self._responses[key]:
            raise RuntimeError(f"No fake response configured for {method} {url}")

        return self._responses[key].pop(0)

    async def close(self) -> None:
        """No-op for fake service."""
        pass

    @property
    def request_log(self) -> list[dict[str, Any]]:
        return self._request_log.copy()

    def clear(self) -> None:
        self._request_log.clear()
        self._responses.clear()
