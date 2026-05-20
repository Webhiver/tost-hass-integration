"""TOST Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PicoApi
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SHOW_DIAGNOSTIC,
    DATA_HOST,
    DATA_SATELLITE_CONFIG,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SHOW_DIAGNOSTIC,
    DOMAIN,
)
from .coordinator import PicoCoordinator, PicoSatelliteConfigCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# hass.data[DOMAIN][entry.entry_id] structure:
#   {DATA_HOST: PicoCoordinator, DATA_SATELLITE_CONFIG: PicoSatelliteConfigCoordinator}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a TOST host from a config entry."""
    session = async_get_clientsession(hass)
    api = PicoApi(session, entry.data[CONF_HOST])

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    host_coordinator = PicoCoordinator(hass, api, scan_interval)

    await host_coordinator.async_config_entry_first_refresh()

    sat_config_coordinator = PicoSatelliteConfigCoordinator(
        hass, session, host_coordinator
    )
    # Kick off the first satellite-config poll. We don't gate setup on it: if
    # all satellites are unreachable on first poll the host integration must
    # still come up.
    await sat_config_coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_HOST: host_coordinator,
        DATA_SATELLITE_CONFIG: sat_config_coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _async_sync_diagnostic_visibility(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)


def _async_sync_diagnostic_visibility(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Enable/disable already-registered diagnostic entities to match the option.

    ``entity_registry_enabled_default`` only applies on first registration; for
    existing entities we have to flip ``disabled_by`` ourselves. We only touch
    entries that we previously disabled (``disabled_by == INTEGRATION``) or
    that are currently enabled — never user-disabled ones.
    """
    show_diag = entry.options.get(CONF_SHOW_DIAGNOSTIC, DEFAULT_SHOW_DIAGNOSTIC)
    ent_reg = er.async_get(hass)
    for entity in list(ent_reg.entities.values()):
        if entity.config_entry_id != entry.entry_id:
            continue
        if entity.entity_category != EntityCategory.DIAGNOSTIC:
            continue
        if show_diag and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            ent_reg.async_update_entity(entity.entity_id, disabled_by=None)
        elif not show_diag and entity.disabled_by is None:
            ent_reg.async_update_entity(
                entity.entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )
