"""Thin async HTTP client for the TOST device API."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT


class PicoApiError(Exception):
    """Raised when a request to the device fails."""


class PicoApi:
    """Wraps the handful of endpoints the integration uses.

    Keeps URL construction and timeout handling in one place so the
    coordinator and config flow only deal with parsed dicts.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self._host = host
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def host(self) -> str:
        return self._host

    @property
    def base_url(self) -> str:
        return f"http://{self._host}"

    async def ping(self) -> bool:
        """Return True iff /api/ping responds with status=ok."""
        try:
            async with self._session.get(
                f"{self.base_url}/api/ping", timeout=self._timeout
            ) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json(content_type=None)
                return isinstance(data, dict) and data.get("status") == "ok"
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def status(self) -> dict[str, Any]:
        """Return the full {state, config, time} status payload."""
        try:
            async with self._session.get(
                f"{self.base_url}/api/status", timeout=self._timeout
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PicoApiError(str(exc)) from exc

        if not isinstance(data, dict):
            raise PicoApiError("Unexpected status payload")
        return data

    async def get_config(self) -> dict[str, Any]:
        """Return the device's full config via GET /api/config."""
        try:
            async with self._session.get(
                f"{self.base_url}/api/config", timeout=self._timeout
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PicoApiError(str(exc)) from exc

        if not isinstance(data, dict):
            raise PicoApiError("Unexpected config payload")
        return data

    async def patch_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Apply a partial config update via PATCH /api/config."""
        try:
            async with self._session.patch(
                f"{self.base_url}/api/config", json=updates, timeout=self._timeout
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    message = (
                        data.get("error") if isinstance(data, dict) else str(data)
                    )
                    raise PicoApiError(message or f"HTTP {resp.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PicoApiError(str(exc)) from exc

        if not isinstance(data, dict):
            raise PicoApiError("Unexpected config response")
        return data
