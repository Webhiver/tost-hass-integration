"""Shared bases for entities that write back to ``config`` on a TOST device.

Two bases:

- ``PicoHostConfigEntity`` — single-device entity attached to the host. Reads
  from the host coordinator's cached config, writes via the host ``PATCH
  /api/config``.
- ``PicoSatelliteConfigEntity`` — per-satellite entity. Reads from the
  satellite config coordinator's cached per-satellite map, writes go
  **directly** to the satellite's own ``/api/config`` (the host is not in the
  data path for v0.7 satellite config — see ``docs/hass-integration.md``).

``_satellite_device_info`` builds the ``DeviceInfo`` block used by every
satellite-scoped entity across platforms so the same device card holds
everything for a given satellite.
"""
from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PicoApiError
from .const import DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import PicoCoordinator, PicoSatelliteConfigCoordinator


def _satellite_device_info(
    coordinator: PicoCoordinator, mac: str
) -> DeviceInfo:
    sat = coordinator.satellite_by_mac(mac) or {}
    sat_state = sat.get("state") if isinstance(sat.get("state"), dict) else {}
    host_mac = coordinator.host_mac or coordinator.api.host
    sat_ip = sat.get("ip")
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        via_device=(DOMAIN, host_mac),
        manufacturer=MANUFACTURER,
        model=DEFAULT_MODEL,
        name=coordinator.satellite_name(mac),
        sw_version=sat_state.get("firmware_version") if sat_state else None,
        configuration_url=f"http://{sat_ip}" if sat_ip else None,
    )


class PicoHostConfigEntity(CoordinatorEntity[PicoCoordinator]):
    """Adds host device-info and a ``_patch_config`` helper.

    Subclasses set ``_attr_unique_id`` and ``_attr_translation_key``; the
    device info attaches them to the single host device shared with the
    climate/sensor entities.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PicoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        mac = coordinator.host_mac or coordinator.api.host
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            manufacturer=MANUFACTURER,
            model=DEFAULT_MODEL,
            name=coordinator.device_name,
            sw_version=coordinator.firmware_version,
            configuration_url=coordinator.api.base_url,
        )

    async def _patch_config(self, updates: dict[str, Any]) -> None:
        try:
            await self.coordinator.api.patch_config(updates)
        except PicoApiError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()


class PicoSatelliteConfigEntity(
    CoordinatorEntity[PicoSatelliteConfigCoordinator]
):
    """Per-satellite entity backed by direct calls to the satellite's API.

    Subclasses pass the host coordinator (used for device info, online flag,
    and IP lookup), the satellite-config coordinator (the
    ``CoordinatorEntity`` super), the satellite MAC, and the config key
    suffix for the unique_id.

    Availability is the AND of:
    1. The satellite-config coordinator's last refresh succeeded.
    2. The host's status says the satellite is paired and online.
    3. The per-satellite config is currently cached (i.e. not in the
       "exceeded failure threshold" state).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        host_coordinator: PicoCoordinator,
        sat_config_coordinator: PicoSatelliteConfigCoordinator,
        mac: str,
        key: str,
    ) -> None:
        super().__init__(sat_config_coordinator)
        self._host = host_coordinator
        self._mac = mac
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_device_info = _satellite_device_info(host_coordinator, mac)

    @property
    def _sat_config(self) -> dict[str, Any] | None:
        return self.coordinator.config_for(self._mac)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        sat = self._host.satellite_by_mac(self._mac)
        if not sat or not sat.get("online"):
            return False
        return self._sat_config is not None

    async def _patch_config(self, updates: dict[str, Any]) -> None:
        try:
            await self.coordinator.patch(self._mac, updates)
        except PicoApiError as exc:
            raise HomeAssistantError(str(exc)) from exc
