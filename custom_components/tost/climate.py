"""Climate entity exposing the host thermostat.

``hvac_mode`` maps directly to ``config.operating_mode`` on the device:

    HVACMode.OFF  ↔ "off"
    HVACMode.HEAT ↔ "manual"
    HVACMode.AUTO ↔ "schedule"

The firmware already treats ``operating_mode == "off"`` as "don't heat"
(``src/thermostat.py``), so there's no separate climate-OFF concept — flipping
the mode is enough. The ``relay_enabled`` switch is a separate kill switch
exposed as ``switch.relay_enabled``; when it's false, ``hvac_action`` reports
``OFF`` even if ``hvac_mode`` says HEAT.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PicoApiError
from .const import DATA_HOST, DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import PicoCoordinator

HVAC_MODE_TO_OPERATING_MODE: dict[HVACMode, str] = {
    HVACMode.OFF: "off",
    HVACMode.HEAT: "manual",
    HVACMode.AUTO: "schedule",
}
OPERATING_MODE_TO_HVAC_MODE: dict[str, HVACMode] = {
    v: k for k, v in HVAC_MODE_TO_OPERATING_MODE.items()
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PicoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_HOST]
    async_add_entities([PicoClimate(coordinator)])


class PicoClimate(CoordinatorEntity[PicoCoordinator], ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "thermostat"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]

    def __init__(self, coordinator: PicoCoordinator) -> None:
        super().__init__(coordinator)
        mac = coordinator.host_mac or coordinator.api.host
        self._attr_unique_id = f"{mac}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            manufacturer=MANUFACTURER,
            model=DEFAULT_MODEL,
            name=coordinator.device_name,
            sw_version=coordinator.firmware_version,
            configuration_url=coordinator.api.base_url,
        )

    @property
    def current_temperature(self) -> float | None:
        effective = self.coordinator.state.get("effective_temperature")
        if effective is not None:
            return effective
        sensor = self.coordinator.state.get("sensor") or {}
        return sensor.get("temperature")

    @property
    def current_humidity(self) -> float | None:
        sensor = self.coordinator.state.get("sensor") or {}
        return sensor.get("humidity")

    @property
    def target_temperature(self) -> float | None:
        # In schedule mode the device drives the target from the active slot
        # (see src/thermostat.py:_effective_target_temperature). Mirror that
        # here so the UI shows what's actually being targeted, not the stale
        # ``config.target_temperature`` (which only applies in manual mode).
        if self.coordinator.config.get("operating_mode") == "schedule":
            active_slot = (self.coordinator.state.get("schedule") or {}).get(
                "active_slot"
            )
            if isinstance(active_slot, dict):
                slot_target = active_slot.get("target_temperature")
                if isinstance(slot_target, (int, float)) and not isinstance(
                    slot_target, bool
                ):
                    return float(slot_target)
        return self.coordinator.config.get("target_temperature")

    @property
    def target_temperature_step(self) -> float:
        return float(self.coordinator.config.get("scale_precision", 0.5))

    @property
    def min_temp(self) -> float:
        return float(self.coordinator.config.get("min_temp", 10))

    @property
    def max_temp(self) -> float:
        return float(self.coordinator.config.get("max_temp", 30))

    @property
    def hvac_mode(self) -> HVACMode:
        operating_mode = self.coordinator.config.get("operating_mode", "manual")
        return OPERATING_MODE_TO_HVAC_MODE.get(operating_mode, HVACMode.HEAT)

    @property
    def hvac_action(self) -> HVACAction:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if not self.coordinator.config.get("relay_enabled", True):
            return HVACAction.OFF
        if self.coordinator.state.get("flame"):
            return HVACAction.HEATING
        return HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if self.coordinator.config.get("operating_mode") == "schedule":
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="schedule_mode_active",
            )
        await self._patch({"target_temperature": float(temperature)})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        operating_mode = HVAC_MODE_TO_OPERATING_MODE.get(hvac_mode)
        if operating_mode is None:
            return
        await self._patch({"operating_mode": operating_mode})

    async def _patch(self, updates: dict[str, Any]) -> None:
        try:
            await self.coordinator.api.patch_config(updates)
        except PicoApiError as exc:
            raise HomeAssistantError(str(exc)) from exc
        await self.coordinator.async_request_refresh()
