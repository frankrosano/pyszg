# Design — HA Standards Alignment for `pyszg` + `szg-hass`

## Overview

Bring `pyszg` (library) and `szg-hass` (integration) into alignment with the
Home Assistant library and integration development standards, **focused on
phase 1 and phase 2**. The work in scope addresses real bugs (broken reauth
chain, swallowed exceptions, dead code), HA quality-scale gaps (no reauth
flow, missing sensor device classes, encapsulation leaks), and small
ergonomic fixes.

This is a refactor in place — no behavior changes for end users beyond
a working reauth flow and slightly tighter sensor states.

**Out of scope** (tracked in Future Work):

- Async `aiohttp` migration of the cloud transport.
- `Appliance.update_from_response` mapping-table refactor.
- PyPI publishing automation.
- Bronze-tier integration test scaffolding for `szg-hass`.

## Architecture

The two-repo split stays intact:

- `pyszg` (library) — protocol, transport, parsing. Sync today, sync after
  this spec.
- `szg-hass` (integration) — HA lifecycle, entities, UI, error mapping.

The relationship between them is what changes. Specifically:

1. **Library is stateless.** Today both `SZGCloudClient` and
   `SZGCloudSignalR` keep their own `_appliances` cache that the
   integration never reads. After this spec the integration's
   `SZGDeviceConnection.appliance` is the only authoritative copy.
2. **Errors flow through.** Today `SZGDeviceConnection.async_refresh`
   swallows everything, breaking HA's reauth path. After this spec,
   library raises typed exceptions and the integration translates them
   into `ConfigEntryAuthFailed` / `UpdateFailed` so HA's recovery
   machinery works.
3. **Connection state is public.** Today entities reach into
   `connection.local_client._stream.connected` and
   `coordinator._signalr`. After this spec, both expose public
   properties (`local_push_active`, `cloud_push_active`).

The phasing splits this into two shippable units:

**Phase 1 — Bug fixes, dead code, hygiene** (no user-facing changes)

- Drop library Appliance caches.
- Exception taxonomy cleanup + integration-side error mapping. Coordinator
  starts surfacing `ConfigEntryAuthFailed` to HA. The reauth *form* still
  doesn't exist yet, so users see HA's generic "credentials expired"
  message — strictly better than today's silent failure loop.
- Public connection-state API.
- Coordinator hygiene (`config_entry=`, `always_update=False`,
  `asyncio.gather`, top-level imports).
- Dead code removal.

**Phase 2 — Reauth flow, sensor classes, options migration**

- Full `async_step_reauth` / `async_step_reauth_confirm` flow.
- `SensorDeviceClass.ENUM` for derived-string sensors.
- `service_required` binary sensor for all appliance types.
- `CONF_DEVICE_PINS` migration from `entry.data` to `entry.options`.

Each phase ends in a working, taggable state.

## Components and Interfaces

### D2. The integration owns appliance state; the library is stateless transport

**Today:** Both `SZGCloudClient` and `SZGCloudSignalR` keep their own
`_appliances: dict[str, Appliance]` dicts that the integration never
reads. The integration keeps its own `SZGDeviceConnection.appliance`.
Three caches, one of which is authoritative.

**Decision:** Library transports return parsed `Appliance` objects (or
delta dicts) but do not retain them. Drop `_appliances` from
`SZGCloudClient` and `SZGCloudSignalR`. The SignalR callback signature
stays as `(device_id, msg_type, data)` — the integration is responsible
for routing into its `SZGDeviceConnection.appliance`.

**Side effects:**

- `SZGCloudSignalR.get_appliance()` is removed.
- `SZGCloudClient.get_appliance_state()` returns a fresh `Appliance`
  each call. The integration merges into its own instance via
  `update_from_response`.

### D3. Exception taxonomy + end-to-end auth-failure surfacing

**Today:** `cloud_signalr.py` does `from .exceptions import ConnectionError`,
shadowing the builtin. Generic `except Exception` in `SZGCloudClient`
hides `URLError`. `SZGDeviceConnection.async_refresh` swallows everything,
so `ConfigEntryAuthFailed` is never raised, so reauth never fires.

**Library side (sync, urllib-based):**

- Drop the `ConnectionError = SZGConnectionError` module-level alias
  outright.
- Internal imports use `SZGConnectionError` everywhere. Fix
  `cloud_signalr.py`'s `from .exceptions import ConnectionError`
  shadowing.
- `SZGCloudClient` catches `urllib.error.HTTPError`,
  `urllib.error.URLError`, and `socket.timeout` explicitly. No bare
  `except Exception:`.

The aiohttp-aware version of these mappings lands later, in the future
async migration spec.

**Integration side:**

`SZGDeviceConnection.async_refresh` is split:

```python
async def async_refresh(self, hass) -> Appliance:
    """Raises AuthenticationError on auth failure; logs other errors."""
    if self.has_local:
        try:
            await hass.async_add_executor_job(self.local_client.refresh)
            self.appliance = self.local_client.appliance
            return self.appliance
        except SZGAuthError:
            raise          # let coordinator surface
        except SZGError as exc:
            _LOGGER.warning("Local refresh failed for %s: %s", self.name, exc)
            # fall through to cloud
    try:
        self.appliance = await self.cloud_client.get_appliance_state(self.device_id)
    except SZGAuthError:
        raise
    except SZGError as exc:
        _LOGGER.debug("Cloud refresh failed for %s: %s", self.name, exc)
    return self.appliance
```

The coordinator's `_async_update_data` translates:

```python
try:
    await asyncio.gather(*[c.async_refresh(self.hass) for c in self.devices.values()])
except SZGAuthError as err:
    raise ConfigEntryAuthFailed("Cloud token rejected") from err
except SZGError as err:
    raise UpdateFailed(str(err)) from err
```

### D4. Reauth flow

**New:** `async_step_reauth` and `async_step_reauth_confirm` in `config_flow.py`.

Flow:

1. Coordinator raises `ConfigEntryAuthFailed` → HA invokes `async_step_reauth`.
2. `async_step_reauth` stores the entry and forwards to
   `async_step_reauth_confirm`.
3. `async_step_reauth_confirm` shows the same form as `async_step_user`
   (auth_url + redirect_url field) — implementation is shared via a
   common `_show_login_form()` helper.
4. On valid code exchange, validate `tokens.user_id` matches the existing
   entry's `unique_id` (catches "logged in with the wrong account").
   If mismatched: abort with `reauth_account_mismatch`.
5. On match, update the entry's `tokens` via `async_update_reload_and_abort`.

`async_step_user` is refactored to share the form-rendering and
code-exchange code with the reauth flow.

### D5. Public connection-state API

**Today:** `SZGLiveReportingModeSensor` reads
`connection.local_client._stream.connected` and `coordinator._signalr`.
`SZGCoordinator.start_signalr_background` reads
`conn._local_push_task.done()`.

**Add to `SZGDeviceConnection`:**

```python
@property
def local_push_active(self) -> bool:
    """True if a local persistent push connection is currently delivering updates."""
    return (
        self._local_push_task is not None
        and not self._local_push_task.done()
        and self.local_client is not None
        and self.local_client.is_push_connected
    )
```

**Add to `SZGClient`:**

```python
@property
def is_push_connected(self) -> bool:
    return self._stream is not None and self._stream.connected
```

**Add to `SZGCoordinator`:**

```python
@property
def cloud_push_active(self) -> bool:
    return self._signalr is not None and self._signalr.is_connected
```

**Add to `SZGCloudSignalR`:**

```python
@property
def is_connected(self) -> bool:
    """True when the WebSocket is open and the SignalR token has not yet expired."""
```

The two diagnostic sensors then read only public attributes.

### D7. Sensor improvements

- `SZGWashCycleSensor`, `SZGWashStatusSensor`, `SZGConnectionModeSensor`,
  `SZGLiveReportingModeSensor` get `_attr_device_class = SensorDeviceClass.ENUM`
  with a fixed `_attr_options` list. Translatable via `translation_key`.
- `service_required` binary sensor moves from refrigerator-only to all
  appliances. It's a global field on every appliance type.
- `uptime` stays as a string sensor for now (parsing the `HHH:MM:SS`
  format to seconds + `SensorDeviceClass.DURATION` is a separate small
  improvement; defer).
- The Sabbath warning on `SZGOperatingModeSelect` keeps its logged
  warning. The `extra_state_attributes` warning string is removed (the
  log message is enough; a `repairs` issue would be ideal but is out of
  scope for this spec).

### D8. Coordinator hygiene

- `super().__init__(..., config_entry=entry, always_update=False)`. The
  dataclass `Appliance` already gets `__eq__` from `@dataclass` and
  `dict` equality is value-based, so `always_update=False` is safe.
- `_async_update_data` uses `asyncio.gather` over per-device refreshes.
- Imports inside functions (`SZGClient` in `async_step_enter_pin`,
  `AuthenticationError as PySZGAuthError` in `async_setup`) move to
  module top.
- `CONF_DEVICE_PINS` migrates from `entry.data` to `entry.options`.
  A `_migrate_v1_to_v2(entry)` helper is called from `async_setup_entry`
  when `entry.version < 2`. Bump `SZGConfigFlow.VERSION` to 2.

### D10. Dead code removal

Removed in this refactor:

- `pyszg/connection.py`: `CATConnection._authenticated` attribute.
- `pyszg/appliance.py`: `Appliance.host` field.
- `pyszg/client.py`: `poll_async`, `start_polling`, `stop_polling`,
  `_polling_task`. (`SZGClient.connect_push` / `read_update` /
  `push_updates` / `disconnect_push` are kept — `szg-hass` uses them.)
- `pyszg/cloud_client.py`: `self._appliances` cache (per D2).
- `pyszg/cloud_signalr.py`: `self._appliances` cache, `get_appliance()`
  (per D2).
- `pyszg/cloud_auth.py`: `login()` interactive flow with
  `redirect_url.txt`. Replaced by an `examples/cloud_login.py` script
  that imports from the library and does the file-handling itself.
  Library-level `exchange_code()` and `get_authorize_url()` stay.
- `pyszg/exceptions.py`: `ConnectionError = SZGConnectionError` alias
  (per D3).

## Data Models

### Library exception hierarchy

```
SZGError                       (base)
├── SZGConnectionError         (transport / network / TLS)
├── SZGTimeoutError            (NEW — was bundled into SZGConnectionError)
├── AuthenticationError        (PIN reject; OAuth refresh failure)
└── CommandError               (appliance NAK; cloud 4xx other than 401)
```

Mapping inside `SZGCloudClient._request`:

| Source                           | Maps to                  |
|----------------------------------|--------------------------|
| HTTP 401                         | `AuthenticationError`    |
| Other HTTP 4xx/5xx               | `CommandError`           |
| `urllib.error.URLError`          | `SZGConnectionError`     |
| Connection refused / DNS failure | `SZGConnectionError`     |
| `socket.timeout`                 | `SZGTimeoutError`        |

### Config entry layout

**Before this spec (version 1):**

```python
entry.data = {
    "tokens": {...},
    "device_pins": {device_id: pin, ...},
}
entry.options = {}
```

**After this spec (version 2):**

```python
entry.data = {
    "tokens": {...},
}
entry.options = {
    "device_pins": {device_id: pin, ...},
}
```

Migration is one-shot at `async_setup_entry` time, guarded by
`entry.version < 2`. After migration, `hass.config_entries.async_update_entry`
is called with `version=2, data=new_data, options=new_options`.

`SZGConfigFlow.VERSION = 2`. The class declares `MINOR_VERSION = 1` (HA
default) so future incremental schema changes can use minor bumps.

### Appliance dataclass

Unchanged in scope, structure, and parsing logic. The flat dataclass with
nested sub-states (`CavityState`, `RefrigerationState`, `KitchenTimerState`)
stays as-is. The mapping-table refactor of `update_from_response` is
deferred to the future-work spec.

## Correctness Properties

### Property 1: Existing appliance tests keep passing

`tests/test_appliance.py` passes unchanged. The `Appliance` dataclass
shape and `update_from_response` semantics are unchanged in this spec
(the mapping-table refactor that would touch them is deferred).

**Validates: Requirements 11.3, 12.1**

### Property 2: No regressions in entity unique_ids

Unique IDs are `{device_id}_{key}`. Don't rename keys; users would lose
entity history.

**Validates: Requirements 9.2, 10.6**

### Property 3: No regressions in entity friendly names

Translations and `_attr_name` strings stay the same.
`SensorDeviceClass.ENUM` adds options translation but keeps the
displayed name unchanged.

**Validates: Requirements 8.3, 8.4, 8.6**

### Property 4: Existing config entries still load after the upgrade

The `_migrate_v1_to_v2` helper is the only path that mutates entry
schema; it is verified by hand-constructing a v1 entry dict and
asserting the post-migration result. After migration, `entry.options`
contains `device_pins` and `entry.data` does not.

**Validates: Requirements 10.2, 10.3, 10.6**

### Property 5: Local push and cloud push remain mutually exclusive on a single device

`SZGCoordinator` skips SignalR-driven updates when `local_push_active`
is true. The new public property must encode the same condition the
current private-attribute check does (task is alive AND stream socket
is connected).

**Validates: Requirements 4.3, 4.6**

## Error Handling

### Library

- All public methods raise `SZGError` subtypes only. Bare `Exception`
  is forbidden in `except` clauses. Caller-visible failures map to
  one of `SZGConnectionError`, `SZGTimeoutError`, `AuthenticationError`,
  `CommandError`.
- `SZGCloudSignalR.connect()` distinguishes recoverable failures
  (network blip → reconnect with backoff) from auth failures (raise
  `AuthenticationError` and let the integration trigger reauth). The
  outer `while self._running` loop must not catch `AuthenticationError`.
- Callback exceptions inside `_connect_and_listen` log with
  `_LOGGER.exception` (full traceback), not `_LOGGER.error` with the
  message only.

### Integration

- `SZGCoordinator._async_update_data` catches `AuthenticationError` →
  raises `ConfigEntryAuthFailed`. Catches other `SZGError` →
  raises `UpdateFailed`. Lets unrelated exceptions propagate.
- `SZGDeviceConnection.async_refresh` does the same per-device, with
  local-then-cloud fallback. Auth errors short-circuit to the caller;
  transport errors fall through.
- `SZGDeviceConnection.async_set_property` raises on auth errors so
  entity service handlers surface a meaningful UI error. Transport
  errors are left to entity-level retry logic (currently none, by
  design — failed commands are a known limitation).
- The reauth flow validates `tokens.user_id == entry.unique_id`. A
  mismatch is reported via `async_step_reauth_confirm`'s `errors`
  dict with key `reauth_account_mismatch`, not silently swallowed.

## Testing Strategy

### `pyszg`

- **Preserve** `tests/test_appliance.py`. Phase 1+2 do not touch
  `update_from_response`; this suite must keep passing without
  modification.
- **New** `tests/test_exceptions.py`. Exercise the mapping from urllib
  / HTTP status / socket errors to library exception types. Patches
  `urllib.request.urlopen` with mocks that raise
  `urllib.error.HTTPError(401, ...)`, `URLError("Connection refused")`,
  `socket.timeout`, etc.
- **New** `tests/test_cloud_client.py`. Unit-tests `SZGCloudClient`'s
  `_request`, `get_devices`, `send_command`, `get_appliance_state`,
  and `set_property` against a patched `urllib.request.urlopen`. The
  test suite is written against the sync API and will need to be
  rewritten when the async migration spec lands — that's expected and
  acceptable.
- All new tests follow the existing repo style (no fixtures package,
  module-scope helper data, plain `pytest` functions).

### `szg-hass`

- No tests exist today. Adding the standard
  `tests/components/szg/conftest.py` + `test_config_flow.py` scaffolding
  is **out of scope for this spec**. It's the obvious next step toward
  Bronze quality scale and will get its own spec.
- Manual verification covers the user-visible changes for this spec:
  reauth flow when a token expires, sensor enum values displayed
  correctly, options-flow PIN entry working unchanged after
  data → options migration.

## Resolved decisions

1. **Scope** → This spec covers phases 1 and 2 only. Async migration,
   `Appliance` mapping-table refactor, and packaging hygiene are
   tracked in Future Work and will be picked up as separate specs.
2. **Local transport (`SZGClient`)** → Stays sync. Async port revisited
   only if scale or external consumers justify it.
3. **`pyszg.ConnectionError` alias** → Dropped outright in phase 1. No
   deprecation shim (no PyPI history, sole consumer updated in the
   same change).
4. **`Appliance` subclassing per type** → Stay flat. Not revisited in
   this spec.

## Future work

These items have been deliberately deferred from this spec. Each can
become its own spec when ready.

- **Async cloud library migration.** Migrate `cloud_auth.py` and
  `cloud_client.py` to `aiohttp`. Drops `async_add_executor_job` for
  cloud calls in `szg-hass`. Breaking API change. Local transport
  stays sync.
- **`Appliance.update_from_response` mapping-table refactor.**
  Maintainability cleanup; replaces ~70 lines of `self.x = resp.get(...)`
  with a declarative mapping table. Public dataclass shape unchanged.
- **Packaging hygiene + PyPI publishing.** `pyproject.toml` URLs,
  tagged releases, manifest pin to git tags, eventually PyPI publishing
  with OIDC trusted publishing.
- **Bronze-tier test scaffolding for `szg-hass`.** `tests/components/szg/`
  with fixtures and a config-flow test suite.
- **`SensorDeviceClass.DURATION` for `uptime`.** Parse `HHH:MM:SS` into
  seconds and surface as a duration sensor.
- **`repairs` issue for Sabbath remote-enable warning.** Replace the
  current logged warning with a proper HA `repairs` issue that the
  user can dismiss.
