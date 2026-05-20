"""DataUpdateCoordinator wrapping a single TOST host."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PicoApi, PicoApiError
from .const import (
    DOMAIN,
    SATELLITE_CONFIG_FAILURE_THRESHOLD,
    SATELLITE_CONFIG_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class PicoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls GET /api/status and exposes typed accessors to entities."""

    def __init__(self, hass: HomeAssistant, api: PicoApi, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.status()
        except PicoApiError as exc:
            raise UpdateFailed(str(exc)) from exc

    @property
    def state(self) -> dict[str, Any]:
        return (self.data or {}).get("state") or {}

    @property
    def config(self) -> dict[str, Any]:
        return (self.data or {}).get("config") or {}

    @property
    def host_mac(self) -> str | None:
        return self.state.get("mac")

    @property
    def device_name(self) -> str:
        name = self.config.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return f"TOST ({self.api.host})"

    @property
    def firmware_version(self) -> str | None:
        return self.state.get("firmware_version")

    @property
    def satellites(self) -> list[dict[str, Any]]:
        """The host's paired satellites with their last-known state."""
        sats = self.state.get("satellites")
        return sats if isinstance(sats, list) else []

    def satellite_by_mac(self, mac: str) -> dict[str, Any] | None:
        target = mac.lower()
        for sat in self.satellites:
            if (sat.get("mac") or "").lower() == target:
                return sat
        return None

    def satellite_name(self, mac: str) -> str:
        """Resolve a satellite's display name from config, falling back to MAC."""
        target = mac.lower()
        for sat in self.config.get("satellites") or []:
            if (sat.get("mac") or "").lower() == target:
                name = sat.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
                break
        return f"Satellite {mac}"


class PicoSatelliteConfigCoordinator(
    DataUpdateCoordinator[dict[str, dict[str, Any] | None]]
):
    """Polls each online satellite's ``/api/config`` directly, in parallel.

    Data shape: ``{mac: config_dict | None}``. ``None`` means the satellite is
    either offline (per the host) or its direct config fetch has failed
    ``SATELLITE_CONFIG_FAILURE_THRESHOLD`` times in a row. The threshold lets
    a single transient timeout pass without flipping entities to
    ``unavailable``.

    Lives next to the host coordinator and reads ``satellites`` from it on
    every poll so satellite churn (pair/unpair) is picked up automatically.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        host_coordinator: PicoCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_satellite_config",
            update_interval=timedelta(seconds=SATELLITE_CONFIG_SCAN_INTERVAL),
        )
        self._session = session
        self._host = host_coordinator
        self._failures: dict[str, int] = {}

    async def _async_update_data(
        self,
    ) -> dict[str, dict[str, Any] | None]:
        satellites = self._host.satellites
        known_macs: set[str] = set()
        targets: list[tuple[str, str]] = []
        for sat in satellites:
            mac = (sat.get("mac") or "").lower()
            ip = sat.get("ip")
            if not mac:
                continue
            known_macs.add(mac)
            if not ip or not sat.get("online"):
                continue
            targets.append((mac, ip))

        prior = self.data or {}
        new_data: dict[str, dict[str, Any] | None] = {
            mac: prior.get(mac) for mac in known_macs
        }

        async def _fetch(mac: str, ip: str) -> tuple[str, dict[str, Any] | None]:
            api = PicoApi(self._session, ip)
            try:
                config = await api.get_config()
            except PicoApiError as exc:
                self._failures[mac] = self._failures.get(mac, 0) + 1
                if self._failures[mac] >= SATELLITE_CONFIG_FAILURE_THRESHOLD:
                    _LOGGER.debug(
                        "Satellite %s config unreachable after %d failures: %s",
                        mac, self._failures[mac], exc,
                    )
                    return mac, None
                # Keep the last-known value while we ride out a transient.
                return mac, prior.get(mac)
            self._failures[mac] = 0
            return mac, config

        if targets:
            for mac, config in await asyncio.gather(
                *(_fetch(m, ip) for m, ip in targets)
            ):
                new_data[mac] = config

        # Forget failure counters for satellites that have been unpaired.
        for mac in list(self._failures.keys()):
            if mac not in known_macs:
                self._failures.pop(mac, None)

        return new_data

    def config_for(self, mac: str) -> dict[str, Any] | None:
        return (self.data or {}).get(mac.lower())

    def is_reachable(self, mac: str) -> bool:
        return self.config_for(mac) is not None

    async def patch(self, mac: str, updates: dict[str, Any]) -> None:
        """PATCH the satellite's own ``/api/config`` directly.

        Raises ``PicoApiError`` on failure; caller should translate to
        ``HomeAssistantError`` if it's surfacing the result through an entity
        action.
        """
        sat = self._host.satellite_by_mac(mac)
        if not sat:
            raise PicoApiError(f"Unknown satellite: {mac}")
        ip = sat.get("ip")
        if not ip:
            raise PicoApiError(f"Satellite {mac} has no known IP yet")

        api = PicoApi(self._session, ip)
        await api.patch_config(updates)
        await self.async_request_refresh()
