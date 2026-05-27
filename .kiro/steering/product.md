# Product

`pyszg` is a Python client library for Sub-Zero Group connected appliances (Sub-Zero, Wolf, Cove). It is reverse-engineered from the official mobile app and Control4 driver — there is no public API.

## Three transports

The library exposes three independent ways to talk to an appliance:

1. **Local IP** (`SZGClient`) — Direct TLS connection to the appliance's CAT module on port 10. Newline-delimited JSON. Works only for older CAT-module appliances. Read-only without a PIN; full control + persistent push stream after `unlock` + `get_async`.
2. **Cloud REST** (`SZGCloudClient`) — Azure AD B2C OAuth2 + PKCE auth, REST calls to `prod.iot.subzero.com`. Works for **all** appliances including newer NGIX/Saber modules. State and commands route through Azure IoT Hub direct methods.
3. **Cloud SignalR** (`SZGCloudSignalR`) — Azure SignalR WebSocket for real-time push. Works for all appliance types. Receives a full state snapshot on connect, then delta updates.

## Module generations

- **CAT** — older modules, all three transports work
- **NGIX / Saber** — newer modules, cloud only (no local IP)

The `ModuleGeneration` enum in `appliance.py` is parsed from the `applianceId` string.

## Sibling repos in this workspace

- `szg-hass` — Home Assistant custom integration that depends on `pyszg` via git. Sets up local push when a PIN is configured and falls back to cloud SignalR / polling otherwise.
- `szg-api-exploration` — Throwaway research scripts, decompiled C4 drivers, and protocol notes. Source of truth when the protocol behavior is unclear; do not import from here.

## Design intent

- **One unified `Appliance` model** across all transports — both local and cloud responses funnel through `Appliance.update_from_response()`. Cloud responses come back as fresh `Appliance` objects from REST and as raw `props` dicts from SignalR; in both cases the consumer is responsible for merging into its own retained state.
- **The library is stateless.** Cloud clients don't cache `Appliance` instances. The HA coordinator (and example scripts) hold the state of record and merge deltas into it. Don't reintroduce caches inside `pyszg` — it makes refresh semantics across two clients (REST + SignalR) ambiguous.
- **Transports do not auto-fall-back inside `pyszg`.** The library exposes them as separate clients; HA-level fallback logic lives in `szg-hass/coordinator.py`.
- **`TokenStore` is the cloud-token contract.** `SZGCloudClient` and `SZGCloudSignalR` both take a `TokenStore` (not a raw `TokenSet`) so they share rotation. Azure AD B2C invalidates the previous refresh_token on every refresh, so persistence has to happen on rotation, not at process exit — the `on_refresh` callback is the supported hook for that. Consumers that don't share a store across clients **will** lose tokens on the next process restart.
- The auth flow is interactive on first run and silent thereafter via refresh token. Sub-Zero's OAuth client registers only custom-scheme redirect URIs that browsers can't open, so the user has to grab the redirect URL from the **DevTools Console** (where the blocked navigation is logged) and paste it back. `examples/cloud_login.py` drives the CLI version of this flow; production consumers (the HA integration's config flow) drive the same flow in-app using `SZGCloudAuth.get_authorize_url` + `exchange_code` directly.
