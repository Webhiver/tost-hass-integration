"""Config and options flows for TOST."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PicoApi, PicoApiError
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SHOW_DIAGNOSTIC,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SHOW_DIAGNOSTIC,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)


class PicoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial 'add device' flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            session = async_get_clientsession(self.hass)
            api = PicoApi(session, host)

            if not await api.ping():
                errors["base"] = "cannot_connect"
            else:
                try:
                    status = await api.status()
                except PicoApiError:
                    errors["base"] = "cannot_connect"
                else:
                    cfg = status.get("config") or {}
                    state = status.get("state") or {}
                    mode = cfg.get("mode")
                    mac = state.get("mac")

                    if mode != "host":
                        errors["base"] = "not_a_host"
                    elif not mac:
                        errors["base"] = "no_mac"
                    else:
                        await self.async_set_unique_id(mac)
                        self._abort_if_unique_id_configured(
                            updates={CONF_HOST: host}
                        )
                        name = cfg.get("name") or f"TOST ({host})"
                        return self.async_create_entry(
                            title=name, data={CONF_HOST: host}
                        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return PicoOptionsFlow(entry)


class PicoOptionsFlow(OptionsFlow):
    """Tunables (poll interval, …) editable after setup."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        current_show_diag = self._entry.options.get(
            CONF_SHOW_DIAGNOSTIC, DEFAULT_SHOW_DIAGNOSTIC
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Required(
                        CONF_SHOW_DIAGNOSTIC, default=current_show_diag
                    ): bool,
                }
            ),
        )
