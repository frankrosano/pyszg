# Implementation Plan

## Overview

This plan executes the design doc in two phases. Phase 1 closes real
bugs and removes dead code in `pyszg`, then wires the matching error
handling into `szg-hass`. Phase 2 adds the user-visible reauth flow,
upgrades sensor classes, and migrates device PINs into the standard
`entry.options` location.

Tasks are ordered by dependency. Within each phase, library work
precedes integration work because the integration imports from the
library. Each task is a single, reviewable unit; tasks within the
same phase share a working tree but should be committed separately
for clean history.

## Notes

Conventions:

- "_Library_" tasks edit `pyszg/`. "_Integration_" tasks edit
  `szg-hass/custom_components/szg/`. Some tasks touch both repos.
- Each task lists `_Requirements: <ids>_` linking back to
  `requirements.md`.
- After each task, run `pytest` in `pyszg/` (and verify the integration
  still imports cleanly) before moving on. The repo should be in a
  working state between every numbered task.

Manifest pinning:

- Tasks 14 and 22 update the `pyszg` git pin in
  `szg-hass/custom_components/szg/manifest.json`. Treat them as
  bookmarks: the work in this spec doesn't go to PyPI, so the pin is
  the only thing that gives users a reproducible install.

## Task Dependency Graph

Tasks group into execution waves. Tasks in the same wave have no
dependencies on each other and can be done in parallel. Each wave
depends on all earlier waves having completed.

```json
{
  "waves": [
    {
      "wave": 1,
      "description": "Phase 1 library bootstrap — exception taxonomy must land first; everything else depends on it",
      "tasks": [1]
    },
    {
      "wave": 2,
      "description": "Phase 1 library — HTTP error mapping and dead code can proceed independently once exceptions exist",
      "tasks": [2, 4, 5, 6]
    },
    {
      "wave": 3,
      "description": "Phase 1 library — SignalR auth fix and tests; SignalR depends on HTTP mapping (task 2), tests cover taxonomy + cloud client (tasks 1, 2, 4), library docs need dead-code removal (task 6)",
      "tasks": [3, 7, 8]
    },
    {
      "wave": 4,
      "description": "Phase 1 integration — independent prep work that does not block on each other",
      "tasks": [9, 13]
    },
    {
      "wave": 5,
      "description": "Phase 1 integration — public-state plumbing depends on the library properties (task 5); auth propagation depends on exception taxonomy and import hoist (tasks 1, 2, 9)",
      "tasks": [10, 12]
    },
    {
      "wave": 6,
      "description": "Phase 1 integration — sensor consumption of public properties (task 11) needs both task 5 (library) and task 10 (integration)",
      "tasks": [11]
    },
    {
      "wave": 7,
      "description": "Phase 1 close — tag the library, pin the integration manifest. Hard prerequisite for phase 2",
      "tasks": [14]
    },
    {
      "wave": 8,
      "description": "Phase 2 — reauth flow + supporting strings; sensor upgrades and service-required can proceed in parallel since none depend on each other",
      "tasks": [15, 16, 17, 18, 19]
    },
    {
      "wave": 9,
      "description": "Phase 2 — options migration depends on coordinator init being current (task 13); README update supports the reauth work",
      "tasks": [20, 21]
    },
    {
      "wave": 10,
      "description": "Phase 2 close — tag and re-pin if any library changes were made during phase 2",
      "tasks": [22]
    }
  ]
}
```

Visual reference (informational, not authoritative):

```
Phase 1 (library)              Phase 1 (integration)         Phase 2
─────────────────              ─────────────────────         ───────
1. Exception taxonomy          9.  Hoist imports             15. Reauth steps
   │                              │                              │ ⇐ uses 9, 12
   ├──▶ 2. HTTP error mapping     10. Public state props       16. Reauth strings
   │       │                          │ ⇐ uses 5                  ⇐ supports 15
   │       ├──▶ 3. SignalR auth      │
   │       │                         11. Use public props      17. Wash sensors
   │       │                             │ ⇐ uses 5, 10           ⇐ independent
   │       └──▶ 4. Drop caches
   │              │                  12. Auth propagation     18. Conn-mode sensors
   │              │                      │ ⇐ uses 1, 2, 9        ⇐ uses 11
   │              │
   │              5. Public props    13. Coordinator init    19. Service-required
   │              │ ⇐ uses 4             ⇐ independent          ⇐ independent
   │              │
   │              └──────────────▶  14. Tag + pin v1          20. Options migration
   │                                    ⇐ closes phase 1         ⇐ uses 13
   │
   6. Dead code                                               21. README update
      ⇐ uses 1                                                   ⇐ supports 15

   7. Tests                                                   22. Tag + pin v2
      ⇐ uses 1, 2, 4                                             ⇐ closes phase 2

   8. Library docs
      ⇐ uses 6
```

## Tasks

## Phase 1 — Library work

- [x] 1. Tighten the library exception taxonomy
  - Add `SZGTimeoutError(SZGError)` to `pyszg/exceptions.py`.
  - Remove the module-level `ConnectionError = SZGConnectionError`
    alias.
  - Update `pyszg/__init__.py` to export `SZGTimeoutError` in
    `__all__` and remove the `ConnectionError` alias from the
    public surface.
  - In `pyszg/cloud_signalr.py`, replace
    `from .exceptions import ConnectionError` with
    `from .exceptions import SZGConnectionError` and update the
    `_LOGGER`/`raise` sites accordingly.
  - Search the repo for any remaining `pyszg.ConnectionError` or
    `from pyszg.exceptions import ConnectionError` imports and
    replace them with `SZGConnectionError`.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 6.6_

- [x] 2. Map cloud HTTP/transport errors to typed exceptions
  - In `SZGCloudClient._request`, replace the bare
    `except Exception` branch with explicit handlers for
    `urllib.error.HTTPError`, `urllib.error.URLError`, and
    `socket.timeout`.
  - HTTP 401 → `AuthenticationError`. Other 4xx/5xx →
    `CommandError`. `URLError` → `SZGConnectionError`.
    `socket.timeout` → `SZGTimeoutError`.
  - Preserve the existing CAT-module quirk where HTTP 500 with body
    `"OK"` is treated as success — keep the comment that explains why.
  - In `SZGCloudClient.send_command`, ensure the same mapping reaches
    callers (no extra wrapping into a generic `SZGError`).
  - _Requirements: 1.1, 1.6, 2.5, 2.6_

- [x] 3. Surface SignalR auth failures instead of looping
  - In `SZGCloudSignalR._connect_and_listen`, when the negotiate POST
    or the access-token exchange returns HTTP 401 (or any other
    indicator that credentials are invalid), let
    `AuthenticationError` propagate.
  - In `SZGCloudSignalR.connect`, narrow the outer reconnect loop's
    `except` clauses so that `AuthenticationError` is not caught.
    `SZGConnectionError` and other `SZGError` subtypes still trigger
    the existing exponential-backoff reconnect.
  - Replace `_LOGGER.error("Callback error: %s", exc)` with
    `_LOGGER.exception("Callback error in SignalR handler")` so
    tracebacks survive.
  - _Requirements: 1.5_

- [x] 4. Drop library-side Appliance caches
  - Remove `self._appliances: dict[str, Appliance] = {}` from
    `SZGCloudClient.__init__`.
  - In `SZGCloudClient.get_appliance_state`, construct and return a
    fresh `Appliance` each call instead of reading and mutating
    `self._appliances[device_id]`.
  - Remove `self._appliances` from `SZGCloudSignalR.__init__`.
  - Remove `SZGCloudSignalR.get_appliance(device_id)`.
  - In `SZGCloudSignalR._connect_and_listen`, remove the inline
    `appliance = self.get_appliance(device_id); appliance.update_from_response(...)`
    block — the library no longer carries state. The callback
    signature `(device_id, msg_type, data)` is unchanged.
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 5. Add public connection-state properties to library transports
  - In `SZGClient`, add `is_push_connected: bool` as a `@property`
    returning `self._stream is not None and self._stream.connected`.
  - In `SZGCloudSignalR`, add `is_connected: bool` as a `@property`
    returning True iff `self._ws is not None`, the WebSocket is open,
    and the cached SignalR access-token expiry has not yet passed.
    Persist `_token_expires_at` from `_get_token_expiry` during
    `_connect_and_listen` so the property has something to read.
  - _Requirements: 4.1, 4.2_

- [x] 6. Remove dead code and the interactive `login()` flow
  - Delete `_authenticated` from `CATConnection.__init__` in
    `pyszg/connection.py`.
  - Delete the `host` field from `Appliance` in `pyszg/appliance.py`,
    along with the `host=host` argument it received in
    `SZGClient.__init__`.
  - Remove `poll_async`, `start_polling`, `stop_polling`, and
    `_polling_task` from `SZGClient`.
  - Remove `SZGCloudAuth.login()` (the `redirect_url.txt`-reading
    interactive flow). Keep `get_authorize_url` and `exchange_code`.
  - Add `examples/cloud_login.py` that uses
    `SZGCloudAuth.get_authorize_url` + `SZGCloudAuth.exchange_code`
    to perform the same browser-and-paste flow that `login()` used
    to do, but as application code, not library code.
  - Confirm with grep that no remaining references to any of the
    removed symbols exist in `pyszg/` or `szg-hass/`.
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7_

- [x] 7. Add unit tests for the new exception taxonomy and the cloud client
  - Add `tests/test_exceptions.py` covering the urllib → library
    mapping table from `design.md`'s Data Models section. At least
    one test per row: HTTP 401, HTTP 5xx, `URLError("Connection refused")`,
    DNS-failure `URLError`, `socket.timeout`. Patch
    `urllib.request.urlopen` with `unittest.mock.patch`.
  - Add `tests/test_cloud_client.py` covering happy-path and one
    failure case for each of `_request`, `get_devices`,
    `get_appliance_state`, `set_property`, and `send_command` via
    the same patching strategy. Use a sample fixture dict for
    `get_devices` modeled on the existing `appliance.py` tests
    (do not import a real fixture file — keep the data inline).
  - Run `pytest` and confirm `tests/test_appliance.py` still passes
    unchanged.
  - _Requirements: 12.1, 12.2, 12.3, 12.4_

- [x] 8. Update library documentation for the phase 1 changes
  - In `pyszg/README.md`, remove all references to
    `client.start_polling`, `poll_async`, and `stop_polling`
    (the symbols no longer exist). If the README's local-push
    section relied on them, swap to the existing
    `connect_push` / `read_update` / `push_updates` example.
  - Replace any reference to `auth.login()` reading `redirect_url.txt`
    with a pointer to `examples/cloud_login.py`.
  - In `pyszg/.kiro/steering/structure.md`, remove `polling` from
    the `SZGClient` description (if mentioned), remove
    `cloud_auth.login()` mention if any, add `examples/cloud_login.py`
    to the `examples/` listing.
  - _Requirements: 13.1, 13.2, 13.3_

## Phase 1 — Integration work

- [x] 9. Hoist function-local imports to module top
  - In `szg-hass/custom_components/szg/config_flow.py`, move
    `from pyszg import SZGClient` and
    `from pyszg.exceptions import AuthenticationError as PySZGAuthError`
    out of `async_step_enter_pin` and put them at module top.
  - In `szg-hass/custom_components/szg/coordinator.py`, move
    `from homeassistant.exceptions import ConfigEntryAuthFailed`
    and `from homeassistant.exceptions import ConfigEntryNotReady`
    out of `async_setup` and put them at module top alongside the
    other HA imports.
  - _Requirements: 5.3, 5.4_

- [x] 10. Add public connection-state properties on the integration side
  - In `coordinator.py`, add a `local_push_active: bool` property
    on `SZGDeviceConnection` that returns True iff
    `self._local_push_task is not None`,
    `not self._local_push_task.done()`,
    `self.local_client is not None`, and
    `self.local_client.is_push_connected` (the new library property).
  - In `coordinator.py`, add a `cloud_push_active: bool` property
    on `SZGCoordinator` that returns
    `self._signalr is not None and self._signalr.is_connected`.
  - _Requirements: 4.3, 4.4_

- [x] 11. Use public properties in the live-reporting sensor and the SignalR dedupe
  - In `sensor.py`, rewrite `SZGLiveReportingModeSensor.native_value`
    so it consults only public attributes:
    `self._connection.local_push_active`,
    `self.coordinator.cloud_push_active`. The three return values
    stay `"Local Push"`, `"Cloud Push (SignalR)"`, `"Cloud Polling"`.
  - In `coordinator.py`'s `start_signalr_background` callback,
    replace the `conn.has_local and conn._local_push_task and not conn._local_push_task.done()`
    test with `conn.local_push_active`.
  - Confirm with grep that no module under `szg-hass/` references
    `_local_push_task`, `_stream`, or `_signalr` on a public type.
  - _Requirements: 4.5, 4.6_

- [x] 12. Propagate auth failures and parallelize coordinator polling
  - Restructure `SZGDeviceConnection.async_refresh` so that
    `pyszg.AuthenticationError` from either the local or cloud path
    re-raises immediately, while other `SZGError` subtypes are
    logged and result in fall-through (local) or silent skip (cloud),
    matching the snippet in `design.md` D3.
  - In `SZGCoordinator._async_update_data`, replace the sequential
    `for conn in self.devices.values(): await conn.async_refresh(...)`
    with `await asyncio.gather(*(conn.async_refresh(self.hass) for conn in self.devices.values()), return_exceptions=False)`.
  - Wrap the gather in `try/except`: catch `pyszg.AuthenticationError`
    → raise `ConfigEntryAuthFailed` with `from`. Catch
    `pyszg.SZGError` → raise `UpdateFailed` with `from`. Let other
    exceptions propagate.
  - Apply the same try/except shape to
    `SZGDeviceConnection.async_set_property` so cloud auth failures
    surface to entity service handlers (don't swallow into a warning).
  - _Requirements: 1.2, 1.3, 1.4, 5.2_

- [x] 13. Adopt current coordinator constructor conventions
  - In `SZGCoordinator.__init__`, pass `config_entry=entry` and
    `always_update=False` as keyword args to `super().__init__`.
  - Manually verify (via `pytest`-free smoke run) that polling
    against an unchanged appliance no longer triggers a state
    write. (`Appliance` dataclass has structural `__eq__`; the
    `raw` dict compares value-wise.)
  - _Requirements: 5.1_

- [-] 14. Bump the pyszg pin in the integration manifest
  - In `szg-hass/custom_components/szg/manifest.json`, change the
    `requirements` entry from
    `pyszg@git+https://github.com/frankrosano/pyszg.git` to a
    pinned ref:
    `pyszg@git+https://github.com/frankrosano/pyszg.git@<phase-1-tag>`.
  - Tag the post-phase-1 `pyszg` commit with `vX.Y.Z` and update the
    pin to that tag.
  - _Requirements: (no direct requirement; supports 11.1 and 13.x by
    keeping installs reproducible)_

## Phase 2 — Reauth flow, sensor classes, options migration

- [ ] 15. Extract a shared login-form helper and add reauth steps
  - Refactor `SZGConfigFlow.async_step_user` so the form rendering
    (PKCE-pair generation, `auth_url` derivation, `async_show_form`
    call) lives in a `_show_login_form(step_id, errors=None)` helper.
  - Refactor the redirect-URL parsing + `exchange_code` call into a
    `_exchange_redirect_url(redirect_url) -> TokenSet | None` helper
    that returns `None` (and populates an `errors` dict via the
    caller) on parse/exchange failure.
  - Add `async_step_reauth(self, entry_data)` that stores
    `self.context["entry_id"]` and forwards to
    `async_step_reauth_confirm`.
  - Add `async_step_reauth_confirm(self, user_input=None)` that
    uses `_show_login_form("reauth_confirm")` for the GET case, and
    on POST validates the `tokens.user_id` against the existing
    entry's `unique_id`. On mismatch, abort with
    `reauth_account_mismatch`. On match, write the new tokens via
    `self.async_update_reload_and_abort(entry, data={...})`.
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 16. Add reauth strings and translations
  - In `strings.json`, add a `config.step.reauth_confirm` block with
    `title`, `description` (mirroring the user step but framing it
    as reauth), and `data.redirect_url` field label.
  - Add `config.abort.reauth_account_mismatch` with text explaining
    the user logged in with a different account.
  - Add `config.error.invalid_url` and `config.error.auth_failed`
    if not already present in the relevant scope.
  - Mirror all of the above into `translations/en.json`.
  - _Requirements: 7.6_

- [ ] 17. Convert wash-cycle and wash-status sensors to ENUM device class
  - In `sensor.py`, on `SZGWashCycleSensor` set
    `_attr_device_class = SensorDeviceClass.ENUM` and
    `_attr_options = [<each WashCycle name title-cased>]`.
  - Same on `SZGWashStatusSensor` for `WashStatus`.
  - Verify `native_value` returns a string from the same option list
    for every reachable enum value (including the `"Unknown ({val})"`
    fallback — change the fallback to map to a stable `"Unknown"`
    option that's also in `_attr_options`, since arbitrary unknown
    integers would otherwise violate the ENUM contract).
  - _Requirements: 8.1, 8.2, 8.5_

- [ ] 18. Convert connection-mode and live-reporting sensors to ENUM
  - In `sensor.py`, on `SZGConnectionModeSensor` set
    `_attr_device_class = SensorDeviceClass.ENUM`,
    `_attr_options = ["Local", "Cloud"]`,
    `_attr_translation_key = "connection_mode"`.
  - On `SZGLiveReportingModeSensor` set
    `_attr_device_class = SensorDeviceClass.ENUM`,
    `_attr_options = ["Local Push", "Cloud Push (SignalR)", "Cloud Polling"]`,
    `_attr_translation_key = "live_reporting_mode"`.
  - In `strings.json` and `translations/en.json` add
    `entity.sensor.connection_mode.state.local` /
    `.cloud` and `entity.sensor.live_reporting_mode.state.*`
    entries for each option.
  - _Requirements: 8.3, 8.4, 8.5, 8.6_

- [ ] 19. Add a service-required binary sensor to every appliance
  - In `binary_sensor.py`, move the existing
    `SZGBinarySensor(coordinator, conn, "service_required", "Service Required", BinarySensorDeviceClass.PROBLEM, diagnostic=True)`
    creation out of the `REFRIGERATOR` branch and add it
    unconditionally for every connection in the loop.
  - Verify the unique-id key remains `service_required` so existing
    refrigerator entities are not duplicated by the registry.
  - _Requirements: 9.1, 9.2_

- [ ] 20. Migrate device PINs from entry.data to entry.options
  - Set `SZGConfigFlow.VERSION = 2`.
  - Add an `async_migrate_entry(hass, entry)` function in
    `__init__.py` that handles `entry.version < 2` by:
    pulling `device_pins` out of `entry.data`, building new `data`
    and `options` dicts, calling
    `hass.config_entries.async_update_entry(entry, version=2,
    data=new_data, options=new_options)`, and returning `True`.
    Older HA's `async_setup_entry` migration path is automatically
    invoked when `version` mismatches.
  - In `SZGCoordinator.async_setup` and
    `SZGCoordinator.async_apply_pin_updates`, change PIN reads from
    `self.entry.data.get(CONF_DEVICE_PINS, {})` to
    `self.entry.options.get(CONF_DEVICE_PINS, {})`.
  - In `SZGOptionsFlow.async_step_enter_pin`, change the save site
    so the new PIN is written via
    `self.hass.config_entries.async_update_entry(entry, options={...})`
    rather than mutating `entry.data`.
  - Manually verify migration: hand-craft a v1 entry dict in a Python
    REPL and call `async_migrate_entry`; confirm `entry.options`
    holds `device_pins` and `entry.data` does not.
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

- [ ] 21. Update the integration README to mention reauth
  - In `szg-hass/README.md`, add a sentence under "Installation" or
    a new "Account Maintenance" subsection noting that if your
    Sub-Zero password changes or your account is logged out, HA will
    surface a reauth notification — click the integration card and
    follow the prompts to log back in. No data loss; entity history
    is preserved.
  - _Requirements: 13.4_

- [ ] 22. Bump pyszg pin and tag phase 2
  - Tag the post-phase-2 `pyszg` commit (no library changes are
    expected in phase 2; this is just a convenience tag).
  - Update the pin in `szg-hass/custom_components/szg/manifest.json`
    if any phase 2 work touched the library after task 14's tag.
    Otherwise leave it pointing at the phase 1 tag.
  - _Requirements: (supports reproducible installs; no direct AC)_
