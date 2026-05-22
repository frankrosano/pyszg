# Requirements Document

HA Standards Alignment for `pyszg` + `szg-hass` (phases 1 and 2).

## Introduction

This spec brings the `pyszg` library and `szg-hass` Home Assistant
integration into alignment with HA's library and integration development
standards, focused on phases 1 and 2 from the design doc. The
requirements below are grouped by phase and mapped to the architectural
decisions (D2, D3, etc.) in `design.md`.

The work fixes real bugs (broken reauth chain, swallowed exceptions),
closes HA quality-scale gaps (no reauth flow, missing device classes,
encapsulation leaks), removes dead code, and standardizes config-entry
layout. No user-visible behavior changes other than the reauth flow
becoming functional and sensors gaining proper enum device classes.

Out of scope: async migration of the cloud transport, the
`Appliance.update_from_response` mapping-table refactor, PyPI
publishing, and Bronze-tier integration tests for `szg-hass`. These
are tracked in `design.md`'s "Future work" section.

## Glossary

- **CAT module** — original "Connected Appliance Technology" hardware
  in older Sub-Zero/Wolf/Cove appliances. Supports local IP control on
  port 10 plus cloud and BLE.
- **Saber / NGIX module** — newer hardware in current models. Cloud
  and BLE only; no local IP.
- **Appliance dataclass** — `pyszg.Appliance`, the unified state model
  shared across all transports.
- **Coordinator** — `SZGCoordinator` in `szg-hass`, an HA
  `DataUpdateCoordinator` that owns per-device state and dispatches
  updates to entities.
- **Device connection** — `SZGDeviceConnection` in `szg-hass`,
  per-appliance state held by the coordinator (transport clients,
  parsed `Appliance`, configured PIN if any).
- **Reauth flow** — HA's standard config-flow step pair
  (`async_step_reauth`, `async_step_reauth_confirm`) used to recover
  expired credentials without removing the integration.
- **Local push** — persistent TLS connection to a CAT module that
  receives delta state updates as newline-delimited JSON.
- **Cloud push (SignalR)** — Azure SignalR WebSocket that receives
  delta state updates for all appliance types.
- **Phase 1 / Phase 2** — sequencing buckets defined in `design.md`'s
  Architecture section. Phase 1 ships first; phase 2 ships after
  phase 1 has been validated.
- **D2, D3, ...** — architectural-decision identifiers from
  `design.md`'s Components and Interfaces section.

## Requirements

## Phase 1: Bug fixes, dead code, hygiene

### Requirement 1: Auth failures propagate to HA

**User Story:** As a Home Assistant user, when my Sub-Zero account's
refresh token becomes invalid (revoked, expired beyond the refresh
window, or the API rejects it), I want the integration to signal to
HA that reauthentication is needed, so that HA can surface the
condition through its standard recovery mechanism instead of failing
silently in the background.

#### Acceptance Criteria

1. WHEN the cloud REST API returns HTTP 401 during a coordinator update
   THEN `SZGCloudClient` SHALL raise `pyszg.AuthenticationError`.
2. WHEN `pyszg.AuthenticationError` is raised inside
   `SZGDeviceConnection.async_refresh` THEN the exception SHALL
   propagate out of the method without being caught or logged as a
   warning.
3. WHEN any per-device refresh raises `pyszg.AuthenticationError`
   inside `SZGCoordinator._async_update_data` THEN the coordinator
   SHALL raise `homeassistant.exceptions.ConfigEntryAuthFailed` with
   the original exception chained via `from`.
4. WHEN any per-device refresh raises a non-auth `pyszg.SZGError`
   inside `SZGCoordinator._async_update_data` THEN the coordinator
   SHALL raise `homeassistant.helpers.update_coordinator.UpdateFailed`
   with the original exception chained via `from`.
5. WHEN `SZGCloudSignalR` receives an authentication failure from the
   negotiate endpoint or the WebSocket access-token exchange THEN it
   SHALL raise `pyszg.AuthenticationError` instead of catching it and
   reconnecting in a loop.
6. WHEN `SZGCloudClient._request` encounters a non-HTTP transport
   error (e.g., DNS failure, connection refused) THEN it SHALL raise
   `pyszg.SZGConnectionError`, NOT a generic `pyszg.SZGError`.

### Requirement 2: Library exception taxonomy is consistent

**User Story:** As a developer integrating `pyszg`, I want a clear,
non-overlapping set of library exception types, so that I can map them
deterministically to HA error states without guessing.

#### Acceptance Criteria

1. WHEN `pyszg/exceptions.py` is read THEN it SHALL define exactly
   these classes: `SZGError`, `SZGConnectionError`, `SZGTimeoutError`,
   `AuthenticationError`, `CommandError`. Each SHALL inherit from
   `SZGError` directly.
2. WHEN `pyszg/exceptions.py` is read THEN it SHALL NOT define a
   module-level `ConnectionError = SZGConnectionError` alias.
3. WHEN `pyszg/__init__.py` is read THEN `__all__` SHALL include
   `SZGTimeoutError` alongside the existing exception exports, and
   SHALL NOT include `ConnectionError` (the alias).
4. WHEN any `pyszg` source file imports an exception from
   `pyszg.exceptions` THEN it SHALL use the `SZG`-prefixed names
   directly (no `as` aliasing to `ConnectionError`).
5. WHEN `SZGCloudClient._request` catches a transport error THEN it
   SHALL classify it as one of: HTTP 401 → `AuthenticationError`,
   other HTTP 4xx/5xx → `CommandError`, `urllib.error.URLError` (incl.
   connection refused, DNS failure) → `SZGConnectionError`,
   `socket.timeout` → `SZGTimeoutError`.
6. WHEN `SZGCloudClient` source is read THEN it SHALL NOT contain a
   bare `except Exception:` clause that converts arbitrary errors to
   `SZGError`.

### Requirement 3: Library is stateless; integration owns appliance state

**User Story:** As a maintainer, I want one authoritative copy of
each appliance's state, so that I don't have to keep multiple caches
in sync and I don't have to wonder which one is correct.

#### Acceptance Criteria

1. WHEN `pyszg/cloud_client.py` is read THEN `SZGCloudClient` SHALL
   NOT carry a `self._appliances` instance-level dict.
2. WHEN `pyszg/cloud_signalr.py` is read THEN `SZGCloudSignalR` SHALL
   NOT carry a `self._appliances` instance-level dict, AND SHALL NOT
   expose a `get_appliance(device_id)` method.
3. WHEN `SZGCloudClient.get_appliance_state(device_id)` is called
   twice in succession THEN it SHALL return two distinct `Appliance`
   instances (no caching).
4. WHEN `SZGCloudSignalR` receives a delta or full-state push message
   THEN its callback signature SHALL remain
   `(device_id: str, msg_type: int, data: dict)` and the library
   SHALL NOT update any internal Appliance state before invoking the
   callback.

### Requirement 4: Connection state is public

**User Story:** As an entity author, I want to read connection state
through public properties, so that my code does not break when
private internals are refactored.

#### Acceptance Criteria

1. WHEN `pyszg.SZGClient` is inspected THEN it SHALL expose
   `is_push_connected: bool` as a `@property` returning True iff a
   persistent push stream is currently open and the underlying socket
   is alive.
2. WHEN `SZGCloudSignalR` is inspected THEN it SHALL expose
   `is_connected: bool` as a `@property` returning True iff the
   WebSocket is currently open and the SignalR access token has not
   yet expired.
3. WHEN `szg_hass.coordinator.SZGDeviceConnection` is inspected THEN
   it SHALL expose `local_push_active: bool` as a `@property`
   returning True iff its background push task is alive AND
   `local_client.is_push_connected` is True.
4. WHEN `szg_hass.coordinator.SZGCoordinator` is inspected THEN it
   SHALL expose `cloud_push_active: bool` as a `@property` returning
   True iff `self._signalr is not None` and `self._signalr.is_connected`.
5. WHEN `szg_hass.sensor.SZGLiveReportingModeSensor` is read THEN
   its implementation SHALL NOT reference any attribute starting with
   an underscore on `SZGDeviceConnection`, `SZGClient`,
   `SZGCloudSignalR`, or `SZGCoordinator`.
6. WHEN `szg_hass.coordinator.SZGCoordinator.start_signalr_background`
   is read THEN its dedupe check that suppresses SignalR updates for
   locally-pushing devices SHALL use `conn.local_push_active`, NOT
   `conn._local_push_task`.

### Requirement 5: Coordinator follows current HA conventions

**User Story:** As a Home Assistant user, I want the integration to
follow current HA conventions so that it doesn't generate deprecation
warnings, doesn't write redundant state, and parallelizes per-device
work.

#### Acceptance Criteria

1. WHEN `SZGCoordinator.__init__` calls `super().__init__` THEN the
   call SHALL pass `config_entry=entry` and `always_update=False` as
   keyword arguments.
2. WHEN `SZGCoordinator._async_update_data` polls multiple devices
   THEN it SHALL invoke per-device refreshes concurrently via
   `asyncio.gather`, NOT sequentially in a `for` loop.
3. WHEN `szg_hass/config_flow.py` is read THEN imports of
   `pyszg.SZGClient` and `pyszg.exceptions.AuthenticationError` SHALL
   appear at module top, NOT inside function bodies.
4. WHEN `szg_hass/__init__.py` and `szg_hass/coordinator.py` are read
   THEN imports of HA exception classes (`ConfigEntryAuthFailed`,
   `ConfigEntryNotReady`) SHALL appear at module top, NOT inside
   function bodies.

### Requirement 6: Dead code is removed

**User Story:** As a maintainer reading the code for the first time,
I want unused symbols removed so that I don't waste time understanding
code that nothing calls.

#### Acceptance Criteria

1. WHEN `pyszg/connection.py` is read THEN `CATConnection` SHALL NOT
   define a `self._authenticated` attribute.
2. WHEN `pyszg/appliance.py` is read THEN the `Appliance` dataclass
   SHALL NOT define a `host` field.
3. WHEN `pyszg/client.py` is read THEN `SZGClient` SHALL NOT define
   `poll_async`, `start_polling`, `stop_polling`, or `_polling_task`
   members.
4. WHEN `pyszg/cloud_auth.py` is read THEN it SHALL NOT contain the
   interactive `login()` method that reads `redirect_url.txt`.
5. WHEN the example `examples/cloud_login.py` is created THEN it
   SHALL import `SZGCloudAuth.get_authorize_url` and
   `SZGCloudAuth.exchange_code` from the library and perform the
   browser/file-handling itself, replacing the removed `login()`.
6. WHEN `pyszg/cloud_signalr.py` is read THEN it SHALL NOT import
   `ConnectionError` from `pyszg.exceptions`. Internal usage SHALL
   reference `SZGConnectionError` directly.
7. WHEN any of the symbols listed in 1–4 above is grep'd for across
   the `pyszg` and `szg-hass` workspaces THEN no remaining references
   SHALL be found.

## Phase 2: Reauth flow, sensor classes, options migration

### Requirement 7: User can re-authenticate without removing the integration

**User Story:** As a Home Assistant user, when my Sub-Zero
authentication expires, I want to log in again from the integration
card, so that I don't have to delete the integration and reconfigure
all my entity customizations.

#### Acceptance Criteria

1. WHEN HA invokes `SZGConfigFlow.async_step_reauth(entry_data)` THEN
   the flow SHALL forward to `async_step_reauth_confirm`.
2. WHEN `async_step_reauth_confirm` is rendered with no user input
   THEN it SHALL display the same auth_url + redirect-URL form as
   `async_step_user`, sharing rendering via a `_show_login_form()`
   helper.
3. WHEN the user submits a redirect URL containing a valid `code`
   parameter to the reauth confirm step THEN the flow SHALL exchange
   the code via `SZGCloudAuth.exchange_code` and obtain a fresh
   `TokenSet`.
4. IF the `tokens.user_id` from the new TokenSet does not match the
   existing `entry.unique_id` THEN the flow SHALL abort with reason
   `reauth_account_mismatch` and SHALL NOT update the entry.
5. WHEN the new `TokenSet`'s `user_id` matches `entry.unique_id`
   THEN the flow SHALL call `async_update_reload_and_abort` with the
   refreshed `tokens` written to `entry.data[CONF_TOKENS]`, and the
   entry SHALL be reloaded automatically.
6. WHEN `strings.json` and `translations/en.json` are read THEN they
   SHALL include user-facing copy for the `reauth_confirm` step,
   `reauth_account_mismatch` abort, and the existing form errors
   (`invalid_url`, `auth_failed`).

### Requirement 8: Enum-valued sensors use SensorDeviceClass.ENUM

**User Story:** As a Home Assistant user, I want sensors that
represent a fixed set of states (wash cycle, wash status, connection
mode, live reporting mode) to use the proper enum device class so
they translate correctly, validate options, and behave consistently
in dashboards and voice assistants.

#### Acceptance Criteria

1. WHEN `SZGWashCycleSensor` is read THEN it SHALL declare
   `_attr_device_class = SensorDeviceClass.ENUM` and
   `_attr_options = [<all WashCycle names, title-cased>]`.
2. WHEN `SZGWashStatusSensor` is read THEN it SHALL declare
   `_attr_device_class = SensorDeviceClass.ENUM` and
   `_attr_options = [<all WashStatus names, title-cased>]`.
3. WHEN `SZGConnectionModeSensor` is read THEN it SHALL declare
   `_attr_device_class = SensorDeviceClass.ENUM`,
   `_attr_options = ["Local", "Cloud"]`, and a `translation_key` that
   maps to entries in `strings.json`.
4. WHEN `SZGLiveReportingModeSensor` is read THEN it SHALL declare
   `_attr_device_class = SensorDeviceClass.ENUM`,
   `_attr_options = ["Local Push", "Cloud Push (SignalR)", "Cloud Polling"]`,
   and a `translation_key`.
5. WHEN any of the enum sensors above returns a value not present in
   `_attr_options` THEN the unit test (or runtime guard) SHALL fail.
   In other words, the enum option list and the `native_value` source
   SHALL be exhaustive for all reachable states.
6. WHEN `strings.json` and `translations/en.json` are read THEN they
   SHALL include `entity.sensor.<key>.state.*` entries for each option
   declared on the connection-mode and live-reporting-mode sensors.

### Requirement 9: Service-required indicator on every appliance

**User Story:** As a Home Assistant user, I want a service-required
binary sensor on every Sub-Zero appliance, not just refrigerators,
so that I can build alerts and automations consistently across my
account.

#### Acceptance Criteria

1. WHEN `binary_sensor.async_setup_entry` is invoked THEN every
   `SZGDeviceConnection` (regardless of `appliance_type`) SHALL
   contribute one `SZGBinarySensor` for the `service_required`
   property.
2. WHEN the `service_required` sensor is created THEN it SHALL have
   `device_class = BinarySensorDeviceClass.PROBLEM`,
   `entity_category = EntityCategory.DIAGNOSTIC`, and the same
   unique-id-key it has today (`service_required`) so existing
   refrigerator entities are not duplicated.

### Requirement 10: Device PINs live in entry.options

**User Story:** As a maintainer, I want options-flow output stored in
`entry.options` (HA convention), so that the data layout is consistent
with other integrations and so that PIN values don't appear in the
config-entry initial-data snapshot used by HA's diagnostics tools.

#### Acceptance Criteria

1. WHEN `SZGConfigFlow.VERSION` is read THEN it SHALL be `2`.
2. WHEN `async_setup_entry` is invoked with a config entry whose
   `version < 2` THEN the integration SHALL invoke a
   `_migrate_v1_to_v2(hass, entry)` helper that:
   - reads `device_pins` from `entry.data` (defaulting to `{}` if absent),
   - calls `hass.config_entries.async_update_entry(entry, version=2,
     data=<entry.data without device_pins>, options=<existing options
     with device_pins added>)`,
   - returns True on success.
3. WHEN `_migrate_v1_to_v2` runs against a v1 entry containing tokens
   and device_pins THEN after migration `entry.data` SHALL contain
   `tokens` only, and `entry.options` SHALL contain `device_pins`.
4. WHEN `SZGOptionsFlow.async_step_enter_pin` saves a new PIN THEN it
   SHALL write to `entry.options[CONF_DEVICE_PINS]`, NOT
   `entry.data[CONF_DEVICE_PINS]`.
5. WHEN `SZGCoordinator.async_setup` and
   `SZGCoordinator.async_apply_pin_updates` read existing PINs THEN
   they SHALL read from `entry.options.get(CONF_DEVICE_PINS, {})`,
   NOT `entry.data`.
6. WHEN an existing v1 entry is loaded after the migration ships
   THEN no entities SHALL change `unique_id`, no devices SHALL be
   re-registered, and the local connection (if previously
   established) SHALL be re-established with the migrated PIN.

## Cross-cutting non-functional requirements

### Requirement 11: Existing public API stays backward-compatible within phase scope

**User Story:** As `szg-hass` (the only consumer of `pyszg`), I want
phase 1 and phase 2 changes to be drop-in upgrades, so that I don't
need a coordinated breaking-change release.

#### Acceptance Criteria

1. WHEN the post-phase-2 `pyszg` is installed against an unchanged
   pre-phase-2 `szg-hass` THEN the integration SHALL still load
   without runtime errors. (The intent is that all signature changes
   to public symbols are additive in phases 1 and 2; breakage is
   reserved for phase 3.)
2. WHEN any public symbol from `pyszg.__init__.py.__all__` (other
   than `ConnectionError`) is imported by name THEN the import SHALL
   succeed.
3. WHEN any `Appliance` instance attribute that exists today (other
   than the deleted `host`) is read THEN it SHALL still exist with
   the same name and type.
4. WHEN any public method on `SZGClient`, `SZGCloudClient`,
   `SZGCloudAuth`, or `SZGCloudSignalR` (other than the removed
   ones listed in Requirement 6) is called with the same arguments
   it accepts today THEN it SHALL behave the same way.

### Requirement 12: Test coverage for new and changed library code

**User Story:** As a maintainer, I want phase 1 and phase 2 changes
covered by unit tests so that future refactors don't silently
regress them.

#### Acceptance Criteria

1. WHEN the test suite runs THEN `tests/test_appliance.py` SHALL pass
   unchanged (no edits to either the test file or the data fixtures
   in it).
2. WHEN the test suite runs THEN a new `tests/test_exceptions.py`
   SHALL cover the urllib → library exception mapping declared in
   Requirement 2.5, with at least one test per row in the mapping
   table.
3. WHEN the test suite runs THEN a new `tests/test_cloud_client.py`
   SHALL cover `SZGCloudClient`'s `_request`, `get_devices`,
   `get_appliance_state`, `set_property`, and `send_command` against
   a patched `urllib.request.urlopen`. Coverage includes the happy
   path and at least one failure case per method.
4. WHEN any of the new tests is run in isolation via
   `pytest tests/test_<name>.py -v` THEN it SHALL pass without
   network access and without external mock servers.

### Requirement 13: Documentation reflects the new layout

**User Story:** As a maintainer or new contributor, I want the
README and steering docs to reflect the new state of the code so
that I don't waste time on outdated information.

#### Acceptance Criteria

1. WHEN `pyszg/README.md` is read THEN any reference to
   `client.start_polling` / `poll_async` / `stop_polling` SHALL be
   removed (the symbols no longer exist per Requirement 6).
2. WHEN `pyszg/README.md` is read THEN any reference to
   `auth.login()` reading `redirect_url.txt` SHALL be replaced by
   a pointer to `examples/cloud_login.py`.
3. WHEN `pyszg/.kiro/steering/structure.md` is read THEN the file
   list and "Where things go" guidance SHALL still match the
   post-refactor source tree (any deleted file removed; any new
   `examples/cloud_login.py` mentioned).
4. WHEN `szg-hass/README.md` is read THEN it SHALL mention that the
   integration supports the standard HA reauthentication flow when
   credentials expire.
