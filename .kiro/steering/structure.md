# Project Structure

```
pyszg/
├── pyproject.toml          # Build config, deps, package discovery
├── README.md               # User-facing docs (kept in sync with public API)
├── LICENSE                 # MIT
├── appliance_details.md    # Notes on per-appliance property mappings
├── src/
│   └── pyszg/
│       ├── __init__.py     # Public API surface — keep __all__ accurate
│       ├── appliance.py    # Appliance dataclass + enums + temp ranges
│       ├── client.py       # SZGClient — local IP transport
│       ├── connection.py   # Low-level TLS socket wrapper for local
│       ├── cloud_auth.py   # OAuth2 PKCE flow, TokenSet, refresh
│       ├── cloud_client.py # SZGCloudClient — REST API
│       ├── cloud_signalr.py# SZGCloudSignalR — async WebSocket push
│       ├── cloud_const.py  # Cloud endpoint URLs, B2C tenant IDs
│       └── exceptions.py   # SZGError hierarchy
├── examples/               # Runnable scripts against real appliances
│   ├── basic_usage.py      # Local: read state
│   ├── get_pin.py          # Local: trigger PIN display
│   ├── push_demo.py        # Local: persistent push stream
│   ├── dump_state.py       # Local: dump full raw state
│   ├── cloud_login.py      # Cloud: one-time interactive OAuth login
│   ├── cloud_usage.py      # Cloud REST: list + read appliances
│   └── cloud_push_demo.py  # Cloud SignalR: real-time push
└── tests/
    ├── __init__.py
    └── test_appliance.py   # Currently the only test file
```

## Where things go

- **New transport-level code** → its own module under `src/pyszg/`. Re-export from `__init__.py`.
- **New parsed appliance properties** → add to `Appliance` dataclass in `appliance.py` and handle in `update_from_response`.
- **New enum values** (e.g. a new cook mode discovered) → extend the `IntEnum` in `appliance.py`. Always include an `UNKNOWN` fallback.
- **New temperature/range constants** → add to `appliance.py` next to existing `TEMP_RANGE_*` constants and re-export from `__init__.py`.
- **New cloud endpoints** → `cloud_const.py`.
- **New exception types** → `exceptions.py`, deriving from `SZGError`.
- **Throwaway protocol probes** → do **not** put them here. Use `../szg-api-exploration/` (sibling workspace).
- **Examples** → `examples/` only when they demonstrate a stable public API. Each example should be runnable as `python3 examples/<name>.py`.
- **Tests** → `tests/test_<module>.py`, mirror the source module name.

## Workspace siblings

This repo is checked out alongside two related repos:

- `../szg-hass/` — Home Assistant custom integration. Imports `pyszg` and adapts it to the HA `DataUpdateCoordinator` pattern. When changing a public API in `pyszg`, check `szg-hass/custom_components/szg/coordinator.py` for callers.
- `../szg-api-exploration/` — Reverse-engineering scratch space (decompiled C4 drivers, captured SignalR negotiate payloads, ad-hoc probe scripts, protocol findings in `subzero_wolf_protocol_findings.md`). Useful when verifying wire-level behavior; nothing here ships.

## Naming conventions

- Public classes prefixed `SZG` (`SZGClient`, `SZGCloudAuth`, `SZGCloudClient`, `SZGCloudSignalR`).
- Public exceptions prefixed `SZG` (`SZGError`, `SZGConnectionError`).
- Property names on the wire are lowercase snake_case strings (`cav_light_on`, `ref_set_temp`, `ice_maker_mode`) — preserve them verbatim when adding new properties; don't rename for "Pythonic" feel.
- `device_id` everywhere refers to the Azure IoT Hub device id from the cloud `get_devices` response.

## Files that should not be edited casually

- `pyproject.toml` version bump → coordinate with a tag and update `szg-hass/manifest.json` requirement at the same time (it pins `pyszg@git+...`).
- `src/pyszg/__init__.py.__all__` → this is the public API contract. Removals are breaking changes.
