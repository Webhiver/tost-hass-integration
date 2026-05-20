"""Binary sensors for host (flame, wifi, sensor problem) and satellites (online, sensor problem)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._entity import _satellite_device_info
from .const import (
    CONF_SHOW_DIAGNOSTIC,
    DATA_HOST,
    DEFAULT_MODEL,
    DEFAULT_SHOW_DIAGNOSTIC,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import PicoCoordinator


@dataclass(frozen=True, kw_only=True)
class PicoBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with a state-block extractor."""

    value_fn: Callable[[dict[str, Any]], bool]


SHARED_BINARY_SENSOR_TYPES: tuple[PicoBinarySensorDescription, ...] = (
    PicoBinarySensorDescription(
        key="sensor_problem",
        translation_key="sensor_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: not bool((s.get("sensor") or {}).get("healthy", False)),
    ),
)


HOST_BINARY_SENSOR_TYPES: tuple[PicoBinarySensorDescription, ...] = (
    PicoBinarySensorDescription(
        key="flame",
        translation_key="flame",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda s: bool(s.get("flame")),
    ),
    PicoBinarySensorDescription(
        key="wifi_connected",
        translation_key="wifi_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: bool(s.get("wifi_connected")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PicoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_HOST]
    show_diag = entry.options.get(CONF_SHOW_DIAGNOSTIC, DEFAULT_SHOW_DIAGNOSTIC)

    def _apply_default(
        entity: BinarySensorEntity, desc: PicoBinarySensorDescription | None
    ) -> BinarySensorEntity:
        if (
            desc is not None
            and desc.entity_category == EntityCategory.DIAGNOSTIC
            and not show_diag
        ):
            entity._attr_entity_registry_enabled_default = False
        return entity

    host_entities: list[BinarySensorEntity] = []
    for description in SHARED_BINARY_SENSOR_TYPES + HOST_BINARY_SENSOR_TYPES:
        host_entities.append(
            _apply_default(
                PicoHostBinarySensor(coordinator, description), description
            )
        )
    async_add_entities(host_entities)

    known: set[str] = set()

    @callback
    def _discover_satellites() -> None:
        new_entities: list[BinarySensorEntity] = []
        for sat in coordinator.satellites:
            mac = (sat.get("mac") or "").lower()
            if not mac or mac in known:
                continue
            known.add(mac)
            new_entities.append(
                _apply_default(PicoSatelliteOnline(coordinator, mac), None)
            )
            for description in SHARED_BINARY_SENSOR_TYPES:
                new_entities.append(
                    _apply_default(
                        PicoSatelliteBinarySensor(coordinator, mac, description),
                        description,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    _discover_satellites()
    entry.async_on_unload(coordinator.async_add_listener(_discover_satellites))


def _host_device_info(coordinator: PicoCoordinator) -> DeviceInfo:
    mac = coordinator.host_mac or coordinator.api.host
    return DeviceInfo(
        identifiers={(DOMAIN, mac)},
        manufacturer=MANUFACTURER,
        model=DEFAULT_MODEL,
        name=coordinator.device_name,
        sw_version=coordinator.firmware_version,
        configuration_url=coordinator.api.base_url,
    )


class _PicoBinarySensorBase(
    CoordinatorEntity[PicoCoordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    entity_description: PicoBinarySensorDescription

    def _state_block(self) -> dict[str, Any] | None:
        raise NotImplementedError

    @property
    def is_on(self) -> bool | None:
        block = self._state_block()
        if block is None:
            return None
        return self.entity_description.value_fn(block)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self._state_block() is not None


class PicoHostBinarySensor(_PicoBinarySensorBase):
    def __init__(
        self,
        coordinator: PicoCoordinator,
        description: PicoBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        mac = coordinator.host_mac or coordinator.api.host
        self._attr_unique_id = f"{mac}_{description.key}"
        self._attr_device_info = _host_device_info(coordinator)

    def _state_block(self) -> dict[str, Any] | None:
        return self.coordinator.state or None


class PicoSatelliteBinarySensor(_PicoBinarySensorBase):
    def __init__(
        self,
        coordinator: PicoCoordinator,
        mac: str,
        description: PicoBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self.entity_description = description
        self._attr_unique_id = f"{mac}_{description.key}"
        self._attr_device_info = _satellite_device_info(coordinator, mac)

    def _state_block(self) -> dict[str, Any] | None:
        sat = self.coordinator.satellite_by_mac(self._mac)
        if not sat:
            return None
        return sat.get("state") if isinstance(sat.get("state"), dict) else None

    @property
    def available(self) -> bool:
        sat = self.coordinator.satellite_by_mac(self._mac)
        if not sat or not sat.get("online"):
            return False
        return super().available


class PicoSatelliteOnline(CoordinatorEntity[PicoCoordinator], BinarySensorEntity):
    """Per-satellite ``connectivity`` indicator.

    Stays available even when the satellite is offline so the user can see
    the disconnect rather than an ``unavailable`` entity that hides it.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator: PicoCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = f"{mac}_online"
        self._attr_device_info = _satellite_device_info(coordinator, mac)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self.coordinator.satellite_by_mac(self._mac) is not None

    @property
    def is_on(self) -> bool:
        sat = self.coordinator.satellite_by_mac(self._mac)
        return bool(sat and sat.get("online"))
