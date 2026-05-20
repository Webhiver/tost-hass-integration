<p align="center"><img src="custom_components/tost/icon.svg" alt="TOST" width="128" height="128"></p>

# TOST — Home Assistant integration

Native Home Assistant support for [TOST — The Open Source Thermostat](https://github.com/Webhiver/tost), a DIY thermostat that runs on a Raspberry Pi Pico 2W.

## What v0.7 ships

- **Climate entity** for the host: target temperature, current temperature, 3-way mode (Off / Manual / Schedule).
- **Sensors** on the host and on every paired satellite: temperature, humidity, WiFi signal (diagnostic), sensor message (diagnostic).
- **Host-only sensors**: effective temperature, flame duration, active schedule slot, next schedule change.
- **Binary sensors** per device: online (satellite only), flame (host), WiFi connected (host, diagnostic), sensor problem (all, diagnostic).
- **Auto-discovery of satellites** through the host's status payload.
- **Number entities** on the host (configuration category): hysteresis, max flame duration, flame cooldown, LED brightness, temperature offset, humidity offset, satellite grace period.
- **Number entities** per satellite (configuration category, v0.7+): LED brightness, temperature offset, humidity offset — written directly to each satellite's `/api/config`.
- **Select entities** on the host (configuration category): operating mode, flame aggregation, flame aggregation sensor (dynamic — `Local` + one option per paired satellite), local sensor policy.
- **Switch entity** on the host (configuration category): relay enable.
- **Switch entity** per satellite (configuration category, v0.7+): relay enable — written directly to each satellite's `/api/config`.
- **Options-flow "Show diagnostic entities" toggle** — off hides WiFi signal, sensor message, WiFi connected, sensor problem by disabling them in the entity registry. User-overridden enables/disables are preserved.

~~The update entity, buttons, and services follow in v0.4~~ — *v0.4 has been dropped from scope: firmware updates and reboots are managed from the device's own web UI.* See `docs/hass-integration.md` in the main repo for the current roadmap.

## Install

### HACS (custom repository)

1. HACS → Integrations → ⋮ → **Custom repositories**.
2. Add `https://github.com/Webhiver/tost` with category **Integration**. HACS picks up the integration from the `hass/` subdirectory.
3. Install **TOST** from the HACS Integrations list.
4. Restart Home Assistant.

### Manual

Copy `hass/custom_components/tost/` into your Home Assistant `config/custom_components/` directory, then restart.

## Configure

Settings → Devices & Services → **Add Integration** → **TOST**.

- Enter the host or IP of the TOST acting as **host** (e.g. `192.168.1.50` or `picothermostat.local` if your router resolves it).
- Satellites cannot be added directly — they appear on the host's web UI as paired devices and are surfaced automatically through the host integration.

### Options

After setup, the integration's **Configure** button exposes:

- **Poll interval** (5–60s, default 10s). Lower is more responsive; below 5s can starve the device's main loop and trip its 8s watchdog.

## Entity behavior notes

- **HVAC modes map to `operating_mode`** on the device:
  - `off` ↔ `operating_mode = "off"` (thermostat algorithm disabled, flame forced off)
  - `heat` ↔ `operating_mode = "manual"` (user-set target temperature) — labeled **Manual**
  - `auto` ↔ `operating_mode = "schedule"` (target temperature driven by the device's schedule) — labeled **Schedule**
- **`set_temperature` while in schedule mode** (`hvac_mode: auto`) is rejected with a clear error. Switch to `heat` (Manual) first — this mirrors the device's own UX and avoids silently overriding the schedule.
- **`relay_enabled` is independent.** The `switch.<name>_relay` entity is a hardware kill switch separate from `hvac_mode`. When it's off, the climate's `hvac_action` reports `off` even if `hvac_mode` is set to `heat`/`auto`.
- **`current_temperature`** reports `effective_temperature` (the aggregated value the thermostat algorithm uses) when available, falling back to the local sensor reading.
- **Satellite config entities (relay switch, LED brightness, temperature/humidity offsets)** talk to each satellite's HTTP API directly — the host is in the data path only for *discovering* satellites (their MAC, IP, and online flag come from the host's `/api/status`). Reads use a separate, slower poll cadence (60s default) than the main `/api/status` loop and skip satellites the host reports as offline. A single transient failure doesn't flip entities to unavailable — three consecutive failures are needed before the cached config is dropped.

## Compatibility

- Home Assistant ≥ 2024.1
- TOST firmware ≥ 1.11.x (uses `/api/status`, `/api/ping`, `/api/config`)

## Branding

`custom_components/tost/icon.svg` holds the source for the integration's logo. Home Assistant's frontend pulls integration card logos from the [`home-assistant/brands`](https://github.com/home-assistant/brands) repository, not from the integration folder — until a PR there lands, the HA UI shows a generic placeholder. To submit the brand, render the SVG to `icon.png` (256×256) and `icon@2x.png` (512×512) and PR them to `custom_integrations/tost/` in that repo.
