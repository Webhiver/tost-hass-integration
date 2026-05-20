"""Constants for the TOST integration."""
from __future__ import annotations

DOMAIN = "tost"
MANUFACTURER = "TOST"
DEFAULT_MODEL = "Pico 2W"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_SHOW_DIAGNOSTIC = "show_diagnostic"

DEFAULT_SCAN_INTERVAL = 10
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 60
DEFAULT_SHOW_DIAGNOSTIC = True

REQUEST_TIMEOUT = 4.0

# Per-satellite /api/config polling. Satellite config rarely changes, so we
# poll on a slower cadence than /api/status. A burst of consecutive failures
# (timeouts, refused connections, …) is needed before we mark the satellite's
# config-backed entities unavailable; one transient miss shouldn't flip them.
SATELLITE_CONFIG_SCAN_INTERVAL = 60
SATELLITE_CONFIG_FAILURE_THRESHOLD = 3

PRESET_MANUAL = "manual"
PRESET_SCHEDULE = "schedule"

# Keys inside ``hass.data[DOMAIN][entry.entry_id]``.
DATA_HOST = "host"
DATA_SATELLITE_CONFIG = "satellite_config"
