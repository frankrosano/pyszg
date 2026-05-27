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
    ├── test_appliance.py   # Appliance dataclass + update_from_response
    ├── test_cloud_client.py# SZGCloudClient REST paths + auth-error mapping
    ├── test_exceptions.py  # Exception hierarchy + transport-error classification
    └── test_token_store.py # TokenStore refresh, callback, locking, sharing
```

## Where things go

- **New transport-level code** → its own module under `src/pyszg/`. Re-export from `__init__.py`.
- **New parsed appliance properties** → add to `Appliance` dataclass in `appliance.py` and handle in `update_from_response`. Don't cache state inside the cloud clients — they're stateless on purpose.
- **New enum values** (e.g. a new cook mode discovered) → extend the `IntEnum` in `appliance.py`. Always include an `UNKNOWN` fallback.
- **New temperature/range constants** → add to `appliance.py` next to existing `TEMP_RANGE_*` constants and re-export from `__init__.py`.
- **New cloud endpoints** → `cloud_const.py`.
- **New exception types** → `exceptions.py`, deriving from `SZGError`. Update the docstring's HA classification table at the same time — `szg-hass` reads it.
- **Cloud token persistence** → don't add it inside the clients. Wire it through `TokenStore(on_refresh=...)`; the callback is the supported persistence hook.
- **Throwaway protocol probes** → do **not** put them here. Use `../szg-api-exploration/` (sibling workspace).
- **Examples** → `examples/` only when they demonstrate a stable public API. Each example should be runnable as `python3 examples/<name>.py`.
- **Tests** → `tests/test_<module>.py`, mirror the source module name. The cloud-side tests patch `urllib.request.urlopen` and run fully offline; keep new tests in that style — no real network calls in CI.

## Workspace siblings

This repo is checked out alongside two related repos:

- `../szg-hass/` — Home Assistant custom integration. Imports `pyszg` and adapts it to the HA `DataUpdateCoordinator` pattern. When changing a public API in `pyszg`, check `szg-hass/custom_components/szg/coordinator.py` for callers.
- `../szg-api-exploration/` — Reverse-engineering scratch space (decompiled C4 drivers, captured SignalR negotiate payloads, ad-hoc probe scripts, protocol findings in `subzero_wolf_protocol_findings.md`). Useful when verifying wire-level behavior; nothing here ships.

## Naming conventions

- Public classes prefixed `SZG` (`SZGClient`, `SZGCloudAuth`, `SZGCloudClient`, `SZGCloudSignalR`). Two non-`SZG` public types — `TokenSet` and `TokenStore` — sit in `cloud_auth.py` and are exported as-is.
- Public exceptions prefixed `SZG` for transport classes (`SZGError`, `SZGConnectionError`, `SZGTimeoutError`); semantic exceptions (`AuthenticationError`, `CommandError`) drop the prefix.
- Property names on the wire are lowercase snake_case strings (`cav_light_on`, `ref_set_temp`, `ice_maker_mode`) — preserve them verbatim when adding new properties; don't rename for "Pythonic" feel.
- `device_id` everywhere refers to the Azure IoT Hub device id from the cloud `get_devices` response.

## Files that should not be edited casually

- `pyproject.toml` version bump → coordinate with a tag and update `szg-hass/manifest.json` requirement at the same time (it pins `pyszg@git+...`). Breaking changes (constructor signatures, removed exports) need a minor-version bump and a coordinated `szg-hass` PR.
- `src/pyszg/__init__.py.__all__` → this is the public API contract. Removals are breaking changes. Current surface includes `TokenStore` and `SZGTimeoutError` (added in 0.2.0/0.3.0); don't drop them without coordinating with `szg-hass/coordinator.py`.
- `cloud_auth.py` `TokenStore` constructor signature and `on_refresh` semantics → this is what `szg-hass` uses to persist rotated tokens to its config entry. Changing the callback contract silently breaks reauth on next HA restart.
