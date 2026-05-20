"""Switch entities backed by host or satellite config.

The host relay switch mirrors the climate entity's HEAT/OFF for automation
convenience. v0.7 adds a per-satellite relay switch — each satellite owns
its own ``relay_enabled`` config key; we PATCH it directly on the satellite
(no host proxy on the data path).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity import PicoHostConfigEntity, PicoSatelliteConfigEntity
from .const import DATA_HOST, DATA_SATELLITE_CONFIG, DOMAIN
from .coordinator import PicoCoordinator, PicoSatelliteConfigCoordinator

RELAY_KEY = "relay_enabled"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    host_coordinator: PicoCoordinator = data[DATA_HOST]
    sat_config_coordinator: PicoSatelliteConfigCoordinator = data[
        DATA_SATELLITE_CONFIG
    ]

    async_add_entities([PicoHostRelaySwitch(host_coordinator)])

    known: set[str] = set()

    @callback
    def _discover_satellites() -> None:
        new_entities: list[SwitchEntity] = []
        for sat in host_coordinator.satellites:
            mac = (sat.get("mac") or "").lower()
            if not mac or mac in known:
                continue
            known.add(mac)
            new_entities.append(
                PicoSatelliteRelaySwitch(
                    host_coordinator, sat_config_coordinator, mac
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    _discover_satellites()
    entry.async_on_unload(
        host_coordinator.async_add_listener(_discover_satellites)
    )


class PicoHostRelaySwitch(PicoHostConfigEntity, SwitchEntity):
    """Mirrors the climate entity's HEAT/OFF, exposed as a switch for automations.

    The climate entity is the primary control; this exists so users can
    ``switch.turn_on`` / ``turn_off`` in scripts without translating from
    HVAC modes.
    """

    _attr_translation_key = RELAY_KEY
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PicoCoordinator) -> None:
        super().__init__(coordinator, RELAY_KEY)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.config.get(RELAY_KEY, True))

    async def async_turn_on(self, **_kwargs: Any) -> None:
        await self._patch_config({RELAY_KEY: True})

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self._patch_config({RELAY_KEY: False})


class PicoSatelliteRelaySwitch(PicoSatelliteConfigEntity, SwitchEntity):
    """Per-satellite relay switch — writes directly to the satellite's API."""

    _attr_translation_key = RELAY_KEY
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        host_coordinator: PicoCoordinator,
        sat_config_coordinator: PicoSatelliteConfigCoordinator,
        mac: str,
    ) -> None:
        super().__init__(
            host_coordinator, sat_config_coordinator, mac, RELAY_KEY
        )

    @property
    def is_on(self) -> bool | None:
        config = self._sat_config
        if not config:
            return None
        return bool(config.get(RELAY_KEY, True))

    async def async_turn_on(self, **_kwargs: Any) -> None:
        await self._patch_config({RELAY_KEY: True})

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self._patch_config({RELAY_KEY: False})
