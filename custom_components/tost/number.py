"""Number entities for tunable config values on host and satellites.

Host entities cover the thermostat-control tunables (hysteresis, durations,
grace period, etc.) and the per-sensor offsets / LED brightness for the
host's own hardware.

A subset of these — LED brightness and the two sensor offsets — is also
meaningful on each paired satellite, since satellites have the same hardware.
v0.7 surfaces those three under each satellite device, written directly to
the satellite's own ``/api/config`` (no host proxy on the data path).
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity import PicoHostConfigEntity, PicoSatelliteConfigEntity
from .const import DATA_HOST, DATA_SATELLITE_CONFIG, DOMAIN
from .coordinator import PicoCoordinator, PicoSatelliteConfigCoordinator


@dataclass(frozen=True, kw_only=True)
class PicoNumberDescription(NumberEntityDescription):
    config_key: str


NUMBER_TYPES: tuple[PicoNumberDescription, ...] = (
    PicoNumberDescription(
        key="hysteresis",
        translation_key="hysteresis",
        config_key="hysteresis",
        native_min_value=0.1,
        native_max_value=5.0,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    PicoNumberDescription(
        key="max_flame_duration",
        translation_key="max_flame_duration",
        config_key="max_flame_duration",
        native_min_value=60,
        native_max_value=86400,
        native_step=60,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    PicoNumberDescription(
        key="flame_cooldown",
        translation_key="flame_cooldown",
        config_key="flame_cooldown",
        native_min_value=0,
        native_max_value=7200,
        native_step=60,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    PicoNumberDescription(
        key="led_brightness",
        translation_key="led_brightness",
        config_key="led_brightness",
        native_min_value=0.0,
        native_max_value=1.0,
        native_step=0.05,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    PicoNumberDescription(
        key="sensor_temperature_offset",
        translation_key="sensor_temperature_offset",
        config_key="sensor_temperature_offset",
        native_min_value=-10,
        native_max_value=10,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    PicoNumberDescription(
        key="sensor_humidity_offset",
        translation_key="sensor_humidity_offset",
        config_key="sensor_humidity_offset",
        native_min_value=-20,
        native_max_value=20,
        native_step=0.5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    PicoNumberDescription(
        key="satellite_grace_period",
        translation_key="satellite_grace_period",
        config_key="satellite_grace_period",
        native_min_value=30,
        native_max_value=600,
        native_step=10,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
)

# The subset replicated per satellite. Each entry must exist in NUMBER_TYPES
# above so that the satellite version reuses the exact same description
# (units, ranges, translation key) as the host counterpart.
_SATELLITE_NUMBER_KEYS = (
    "led_brightness",
    "sensor_temperature_offset",
    "sensor_humidity_offset",
)
SATELLITE_NUMBER_TYPES: tuple[PicoNumberDescription, ...] = tuple(
    desc for desc in NUMBER_TYPES if desc.key in _SATELLITE_NUMBER_KEYS
)


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

    async_add_entities(
        PicoHostNumber(host_coordinator, description)
        for description in NUMBER_TYPES
    )

    known: set[str] = set()

    @callback
    def _discover_satellites() -> None:
        new_entities: list[NumberEntity] = []
        for sat in host_coordinator.satellites:
            mac = (sat.get("mac") or "").lower()
            if not mac or mac in known:
                continue
            known.add(mac)
            for description in SATELLITE_NUMBER_TYPES:
                new_entities.append(
                    PicoSatelliteNumber(
                        host_coordinator,
                        sat_config_coordinator,
                        mac,
                        description,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    _discover_satellites()
    entry.async_on_unload(
        host_coordinator.async_add_listener(_discover_satellites)
    )


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class PicoHostNumber(PicoHostConfigEntity, NumberEntity):
    entity_description: PicoNumberDescription

    def __init__(
        self,
        coordinator: PicoCoordinator,
        description: PicoNumberDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return _coerce_float(
            self.coordinator.config.get(self.entity_description.config_key)
        )

    async def async_set_native_value(self, value: float) -> None:
        await self._patch_config({self.entity_description.config_key: value})


class PicoSatelliteNumber(PicoSatelliteConfigEntity, NumberEntity):
    """Per-satellite number — writes directly to the satellite's API."""

    entity_description: PicoNumberDescription

    def __init__(
        self,
        host_coordinator: PicoCoordinator,
        sat_config_coordinator: PicoSatelliteConfigCoordinator,
        mac: str,
        description: PicoNumberDescription,
    ) -> None:
        super().__init__(
            host_coordinator, sat_config_coordinator, mac, description.key
        )
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        config = self._sat_config
        if not config:
            return None
        return _coerce_float(config.get(self.entity_description.config_key))

    async def async_set_native_value(self, value: float) -> None:
        await self._patch_config({self.entity_description.config_key: value})
