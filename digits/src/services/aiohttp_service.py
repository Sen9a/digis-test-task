from typing import Any

import aiohttp

from src.services.base import APIService


class AiohttpAPIService(APIService):
    """
    HTTP service using aiohttp.

    Creates a new ClientSession per request. Simple, no lifecycle
    management needed. Session is opened and closed with each call.
    """

    def __init__(
        self,
        base_url: str = "",
        default_headers: dict[str, str] | None = None,
        auth: aiohttp.BasicAuth | None = None,
        timeout: float = 30.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._default_headers = default_headers or {}
        self._auth = auth
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        full_url = f"{self._base_url}{url}" if self._base_url else url
        merged = {**self._default_headers, **(headers or {})}

        async with aiohttp.ClientSession(
            auth=self._auth,
            timeout=self._timeout,
        ) as session:
            async with session.get(full_url, params=params, headers=merged) as response:
                body = await response.json()
                return body, response.status

    async def post(
        self,
        url: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], int]:
        full_url = f"{self._base_url}{url}" if self._base_url else url
        merged = {**self._default_headers, **(headers or {})}

        async with aiohttp.ClientSession(
            auth=self._auth,
            timeout=self._timeout,
        ) as session:
            async with session.post(full_url, json=body, headers=merged) as response:
                response_body = await response.json()
                return response_body, response.status
