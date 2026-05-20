"""Sensors for host and satellites.

The ``state`` payload on the host and the trimmed satellite payload share the
same shape for the fields we read (``sensor``, ``wifi_strength``), so a single
``value_fn`` works on both. Host-only sensors (effective temperature, flame
duration, schedule slot, next slot change) live in a separate description
tuple and are only registered against the host device.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
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
class PicoSensorDescription(SensorEntityDescription):
    """Sensor description with extractors that operate on a state block."""

    value_fn: Callable[[dict[str, Any]], Any]
    healthy_fn: Callable[[dict[str, Any]], bool] = lambda _s: True


def _sensor_message(s: dict[str, Any]) -> str | None:
    msg = (s.get("sensor") or {}).get("message")
    if not isinstance(msg, str) or not msg.strip():
        return None
    return msg.strip()


def _active_slot_index(s: dict[str, Any]) -> int | None:
    idx = (s.get("schedule") or {}).get("active_slot_index")
    if isinstance(idx, bool) or not isinstance(idx, int):
        return None
    return idx


def _next_change_at(s: dict[str, Any]) -> datetime | None:
    schedule = s.get("schedule") or {}
    if not schedule.get("time_synced"):
        return None
    epoch = schedule.get("next_change_at")
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool):
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc)


SHARED_SENSOR_TYPES: tuple[PicoSensorDescription, ...] = (
    PicoSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda s: (s.get("sensor") or {}).get("temperature"),
        healthy_fn=lambda s: bool((s.get("sensor") or {}).get("healthy", True)),
    ),
    PicoSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda s: (s.get("sensor") or {}).get("humidity"),
        healthy_fn=lambda s: bool((s.get("sensor") or {}).get("healthy", True)),
    ),
    PicoSensorDescription(
        key="wifi_strength",
        translation_key="wifi_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("wifi_strength"),
    ),
    PicoSensorDescription(
        key="sensor_message",
        translation_key="sensor_message",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_sensor_message,
    ),
)


HOST_SENSOR_TYPES: tuple[PicoSensorDescription, ...] = (
    PicoSensorDescription(
        key="effective_temperature",
        translation_key="effective_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda s: s.get("effective_temperature"),
    ),
    PicoSensorDescription(
        key="flame_duration",
        translation_key="flame_duration",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=lambda s: s.get("flame_duration"),
    ),
    PicoSensorDescription(
        key="active_slot_index",
        translation_key="active_slot_index",
        value_fn=_active_slot_index,
    ),
    PicoSensorDescription(
        key="next_slot_change",
        translation_key="next_slot_change",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_change_at,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PicoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_HOST]
    show_diag = entry.options.get(CONF_SHOW_DIAGNOSTIC, DEFAULT_SHOW_DIAGNOSTIC)

    def _apply_default(entity: SensorEntity, desc: PicoSensorDescription) -> SensorEntity:
        if desc.entity_category == EntityCategory.DIAGNOSTIC and not show_diag:
            entity._attr_entity_registry_enabled_default = False
        return entity

    host_entities: list[SensorEntity] = []
    for description in SHARED_SENSOR_TYPES + HOST_SENSOR_TYPES:
        host_entities.append(
            _apply_default(PicoHostSensor(coordinator, description), description)
        )
    async_add_entities(host_entities)

    known: set[str] = set()

    @callback
    def _discover_satellites() -> None:
        new_entities: list[SensorEntity] = []
        for sat in coordinator.satellites:
            mac = (sat.get("mac") or "").lower()
            if not mac or mac in known:
                continue
            known.add(mac)
            for description in SHARED_SENSOR_TYPES:
                new_entities.append(
                    _apply_default(
                        PicoSatelliteSensor(coordinator, mac, description),
                        description,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    _discover_satellites()
    entry.async_on_unload(coordinator.async_add_listener(_discover_satellites))


class _PicoSensorBase(CoordinatorEntity[PicoCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: PicoSensorDescription

    def _state_block(self) -> dict[str, Any] | None:
        raise NotImplementedError

    @property
    def native_value(self) -> Any:
        block = self._state_block()
        if not block:
            return None
        return self.entity_description.value_fn(block)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        block = self._state_block()
        if not block:
            return False
        if not self.entity_description.healthy_fn(block):
            return False
        return self.entity_description.value_fn(block) is not None


class PicoHostSensor(_PicoSensorBase):
    def __init__(
        self,
        coordinator: PicoCoordinator,
        description: PicoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        mac = coordinator.host_mac or coordinator.api.host
        self._attr_unique_id = f"{mac}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            manufacturer=MANUFACTURER,
            model=DEFAULT_MODEL,
            name=coordinator.device_name,
            sw_version=coordinator.firmware_version,
            configuration_url=coordinator.api.base_url,
        )

    def _state_block(self) -> dict[str, Any] | None:
        return self.coordinator.state or None


class PicoSatelliteSensor(_PicoSensorBase):
    def __init__(
        self,
        coordinator: PicoCoordinator,
        mac: str,
        description: PicoSensorDescription,
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
