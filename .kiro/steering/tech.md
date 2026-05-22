# Tech Stack

## Language & runtime

- **Python ≥ 3.10** (per `pyproject.toml`). System Python is 3.14 via Homebrew on Apple Silicon — see global `python-environment.md` for `uv` / `pip3` rules.
- Library code is synchronous except `SZGCloudSignalR`, which is `asyncio` + `websockets`.

## Build system

- `setuptools` build backend, `pyproject.toml` only (no `setup.py`).
- Source layout: `src/pyszg/` with `[tool.setuptools.packages.find] where = ["src"]`.
- Single runtime dependency: `websockets >= 12.0` (only needed for SignalR — `__init__.py` imports it under `try/except ImportError` and sets `SZGCloudSignalR = None` if missing).

## Dependencies

| Purpose | Library |
|---|---|
| WebSocket transport (SignalR) | `websockets` |
| Local TLS (port 10, self-signed) | `ssl` (stdlib), `socket` (stdlib) |
| Cloud REST | `urllib` (stdlib), `http.client` (stdlib) |
| OAuth2 PKCE | `hashlib`, `secrets`, `base64` (stdlib) |
| Browser launch for login | `webbrowser` (stdlib) |
| Tests | `pytest`, `pytest-asyncio` (extras: `dev`) |

Stdlib-first is intentional — keeps the install footprint small for downstream HACS users.

## Common commands

```bash
# Editable install for development
uv pip install -e .

# With dev extras (pytest, pytest-asyncio)
uv pip install -e '.[dev]'

# Run tests
pytest

# Run a single test file
pytest tests/test_appliance.py -v

# Try the library against a real appliance (local)
python3 examples/basic_usage.py 192.168.1.100 123456
python3 examples/get_pin.py 192.168.1.100              # door must be open
python3 examples/push_demo.py 192.168.1.100 123456

# Try the library against a real appliance (cloud)
python3 examples/cloud_usage.py
python3 examples/cloud_push_demo.py
```

If `uv` is not available, fall back to `pip3 install --break-system-packages -e .` (Homebrew Python is PEP 668 externally-managed).

## Code style conventions

- `from __future__ import annotations` at the top of every module.
- Type hints everywhere; PEP 604 union syntax (`str | None`).
- Public exceptions all derive from `SZGError`. Re-export `SZGConnectionError` as `ConnectionError` alias for ergonomics.
- Enums are `IntEnum` (matches the wire protocol's integer codes for cook modes, wash cycles, etc.).
- Constants like `TEMP_RANGE_FRIDGE` live alongside the model they describe (`appliance.py`), not in a separate `const.py`.
- Public surface is whatever is listed in `src/pyszg/__init__.py.__all__`. Keep it in sync.

## Protocol gotchas (worth knowing before changing transport code)

- Local TLS uses a **self-signed cert and TLS 1.3**; verification is disabled. Do not "fix" this.
- SignalR negotiate requires the userId to be **lowercased** before the POST.
- Push updates are **delta-only** — `props` contains only changed keys. Always merge into existing state via `Appliance.update_from_response`.
- The local connection has two modes: single-shot (one command, close) and persistent (after `unlock` + `get_async`, the socket stays open and receives push frames). Don't mix them.
