"""Select entities for enumerated config values on the host."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._entity import PicoHostConfigEntity
from .const import DATA_HOST, DOMAIN
from .coordinator import PicoCoordinator


@dataclass(frozen=True, kw_only=True)
class PicoSelectDescription(SelectEntityDescription):
    config_key: str


SELECT_TYPES: tuple[PicoSelectDescription, ...] = (
    PicoSelectDescription(
        key="operating_mode",
        translation_key="operating_mode",
        config_key="operating_mode",
        options=["off", "manual", "schedule"],
        entity_category=EntityCategory.CONFIG,
    ),
    PicoSelectDescription(
        key="flame_mode",
        translation_key="flame_mode",
        config_key="flame_mode",
        options=["average", "all", "any", "one"],
        entity_category=EntityCategory.CONFIG,
    ),
    PicoSelectDescription(
        key="local_sensor",
        translation_key="local_sensor",
        config_key="local_sensor",
        options=["included", "fallback"],
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PicoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_HOST]
    entities: list[SelectEntity] = [
        PicoSelect(coordinator, description) for description in SELECT_TYPES
    ]
    entities.append(PicoFlameModeSensorSelect(coordinator))
    async_add_entities(entities)


class PicoSelect(PicoHostConfigEntity, SelectEntity):
    """Generic config-key select with a fixed option list."""

    entity_description: PicoSelectDescription

    def __init__(
        self,
        coordinator: PicoCoordinator,
        description: PicoSelectDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.config.get(self.entity_description.config_key)
        if isinstance(value, str) and value in (self.options or ()):
            return value
        return None

    async def async_select_option(self, option: str) -> None:
        await self._patch_config({self.entity_description.config_key: option})


class PicoFlameModeSensorSelect(PicoHostConfigEntity, SelectEntity):
    """Picks which sensor drives ``flame_mode == "one"``.

    Options are dynamic: ``local`` plus one per paired satellite, labeled by
    the satellite's name. Internally the device stores the MAC; we translate
    in both directions so HA users see friendly labels.
    """

    _attr_translation_key = "flame_mode_sensor"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: PicoCoordinator) -> None:
        super().__init__(coordinator, "flame_mode_sensor")

    def _options_map(self) -> dict[str, str]:
        """Map display label → device value (``local`` or a MAC)."""
        result: dict[str, str] = {"local": "local"}
        seen: set[str] = set()
        for sat in self.coordinator.config.get("satellites") or []:
            mac = (sat.get("mac") or "").lower()
            if not mac:
                continue
            seen.add(mac)
            raw_name = sat.get("name")
            base = (
                raw_name.strip()
                if isinstance(raw_name, str) and raw_name.strip()
                else mac
            )
            label = base
            suffix = 2
            while label in result:
                label = f"{base} ({suffix})"
                suffix += 1
            result[label] = mac

        # Keep an orphan MAC visible if it's still the active selection,
        # so the entity doesn't go to ``unknown`` after an unpair.
        current = str(self.coordinator.config.get("flame_mode_sensor") or "").lower()
        if current and current != "local" and current not in seen:
            result.setdefault(current, current)
        return result

    @property
    def options(self) -> list[str]:
        return list(self._options_map().keys())

    @property
    def current_option(self) -> str | None:
        current = str(self.coordinator.config.get("flame_mode_sensor") or "local").lower()
        for label, value in self._options_map().items():
            if value == current:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        value = self._options_map().get(option)
        if value is None:
            raise HomeAssistantError(f"Unknown flame mode sensor: {option}")
        await self._patch_config({"flame_mode_sensor": value})
