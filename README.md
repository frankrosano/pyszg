# pyszg

Python client for Sub-Zero Group (Sub-Zero, Wolf, Cove) connected appliances.

Supports three communication methods:
- **Local IP** — Direct TLS connection to the appliance's CAT module (no cloud, no account needed)
- **Cloud REST** — OAuth2 REST API for reading state and sending commands (all appliance types)
- **Cloud SignalR** — Real-time push updates via WebSocket (all appliance types)

## Install

```bash
pip install -e .
```

## Local IP Control

For appliances with a CAT module (supports Control4/Crestron/Savant):

```python
from pyszg import SZGClient

# Read-only (no PIN needed)
client = SZGClient("192.168.1.100")
client.refresh_minimal()
print(client.appliance.model)

# Full access with PIN
client = SZGClient("192.168.1.100", pin="123456")
client.refresh()
print(client.appliance.cavity1.temp)

# Control
client.set_property("cav_light_on", True)
```

### Local Real-Time Push

After authenticating, the local connection stays open and pushes state changes instantly:

```python
client = SZGClient("192.168.1.100", pin="123456")
client.connect_push()

for update in client.push_updates():
    props = update.get("props", {})
    for key, value in props.items():
        print(f"{key} = {value}")
```

### Getting the PIN

A door on the appliance must be physically open, then:

```bash
python examples/get_pin.py 192.168.1.100
```

The 6-digit PIN appears on the appliance's display. It's static and reusable.

## Cloud REST API

For all appliances, including newer NGIX/Saber modules without local IP access:

```python
from pyszg import SZGCloudAuth, SZGCloudClient, TokenStore

# First time — run examples/cloud_login_password.py to sign in with your
# Owner's App email + password and write tokens to a file:
#   python examples/cloud_login_password.py tokens.json
# (browser fallback: python examples/cloud_login.py tokens.json)
auth = SZGCloudAuth()
tokens = auth.load_tokens("tokens.json")

# Wrap the tokens in a TokenStore. Azure AD B2C rotates the refresh token on
# every refresh, so on_refresh must persist the new tokens or the next run
# will fail. Share the same store between SZGCloudClient and SZGCloudSignalR.
store = TokenStore(
    tokens,
    auth,
    on_refresh=lambda new_tokens: auth.save_tokens(new_tokens, "tokens.json"),
)

# List all appliances on the account (refreshes silently when expired)
client = SZGCloudClient(store)
devices = client.get_devices()

# Get appliance state (works for all module types)
appliance = client.get_appliance_state(device_id)
print(appliance.model)

# Control
client.set_property(device_id, "ref_set_temp", 37)
```

## Cloud Real-Time Push (SignalR)

Real-time push updates for all appliances via Azure SignalR:

```python
import asyncio
from pyszg import SZGCloudAuth, SZGCloudSignalR, TokenStore

auth = SZGCloudAuth()
tokens = auth.load_tokens("tokens.json")
store = TokenStore(
    tokens,
    auth,
    on_refresh=lambda new_tokens: auth.save_tokens(new_tokens, "tokens.json"),
)

signalr = SZGCloudSignalR(store)

def on_update(device_id, msg_type, data):
    if msg_type == 2:  # delta update
        props = data.get("props", {})
        print(f"{device_id}: {props}")

asyncio.run(signalr.connect(callback=on_update))
```

### Cloud Auth Flow

The preferred way to sign in is `SZGCloudAuth.login_with_password`, which
drives the Sub-Zero sign-in page over plain HTTP (no browser). The password
is used once and never stored. Run the example once to produce a tokens file:

```bash
python examples/cloud_login_password.py tokens.json
```

Or from code:

```python
from pyszg import AuthenticationError, InteractiveLoginRequired, SZGCloudAuth

auth = SZGCloudAuth()
try:
    tokens = auth.login_with_password(email, password)
except InteractiveLoginRequired:
    ...  # MFA / extra step / changed page: use the browser flow below
except AuthenticationError:
    ...  # wrong email or password (don't retry blindly: lockout risk)
```

`InteractiveLoginRequired` is a subclass of `AuthenticationError`, so catch
it first. Network problems and Sub-Zero outages raise `SZGConnectionError` /
`SZGTimeoutError`; a transient token-endpoint failure is retried a few times
before giving up.

#### Fallback: browser-and-paste flow

Use this if `login_with_password` raises `InteractiveLoginRequired` (for example
if Sub-Zero changes the sign-in page or adds MFA, which it does not currently offer). The
library exposes the OAuth building blocks and the interactive flow lives in
`examples/cloud_login.py`:

```bash
python examples/cloud_login.py tokens.json
```

Under the hood:

1. The script opens the Sub-Zero login page in your browser.
2. Log in with your Sub-Zero Owner's App credentials.
3. The browser tries to redirect to a URL starting with `msauth.com.subzero.group.owners.app://auth?code=...` but browsers can't resolve that scheme. The page won't load — that's expected.
4. Open developer tools (F12 or Cmd+Option+I) → **Console** tab. The blocked navigation logs there with the full redirect URL.
5. Copy that URL into `redirect_url.txt`, then press Enter in the terminal.
6. The script exchanges the code via `SZGCloudAuth.exchange_code` and writes the resulting tokens to disk.
6. Subsequent calls use `auth.ensure_valid()` to silently refresh — no browser needed.

## Supported Appliances

| Module | Local IP | Cloud REST | Cloud SignalR | Found In |
|--------|:---:|:---:|:---:|----------|
| **CAT** | ✓ | ✓ | ✓ | Older appliances with Control4/Crestron/Savant support |
| **NGIX / Saber** | ✗ | ✓ | ✓ | Newer appliances (Alexa/Google only) |

Check compatibility at: https://www.subzero-wolf.com/assistance/answers/multi-brand/connect-appliances-to-third-party-systems

| Brand | Model | Local IP | Cloud | Notes |
|-------|-------|:---:|:---:|-------|
| Sub-Zero | Classic (CL) | ✗ | ✓ | Newer module |
| Sub-Zero | Legacy Classic (BI) | ✓* | ✓ | Depends on serial number |
| Sub-Zero | Designer (DET, DEC, DEU) | ✗ | ✓ | Newer module |
| Sub-Zero | Legacy Designer (IT, IC, ID) | ✓ | ✓ | From serial 5700000+ |
| Wolf | Dual Fuel Range | ✗ | ✓ | Newer module |
| Wolf | Induction Range | ✓* | ✓ | Depends on serial number |
| Wolf | Legacy E Series Oven (-2 model) | ✓ | ✓ | Needs control board update |
| Wolf | Current E Series Oven | ✓ | ✓ | Full support |
| Wolf | M Series Oven | ✓ | ✓ | From serial 17459188+ |
| Cove | Current Dishwasher | ✗ | ✓ | Newer module |
| Cove | Legacy Dishwasher | ✓* | ✓ | Depends on serial number |

\* May require a replacement connected module.

## Protocol Details

### Local IP (CAT modules)
- TLS 1.3 on port 10 (self-signed cert)
- Newline-delimited JSON commands
- Unauthenticated: single command per connection, minimal state
- Authenticated (unlock + get_async): returns full state AND keeps connection open for real-time push
- Push updates are delta-only: `{"msg_types": 2, "seq": N, "timestamp": "ISO8601", "props": {"key": value}}`

### Cloud REST API
- Azure AD B2C OAuth2 + PKCE authentication
- REST API at `prod.iot.subzero.com`
- Direct method calls to appliances via Azure IoT Hub
- Full state available for all module types via the `get` command

### Cloud SignalR
- Real-time push via Azure SignalR Service WebSocket
- Negotiate endpoint: `POST /signal-r/negotiateUser` (requires lowercase userId)
- Receives full state on connect, then delta updates for all state changes
- Works with all appliance types (CAT, NGIX, Saber)

## Examples

```bash
# Cloud: one-time email + password login (writes cloud_tokens.json)
python examples/cloud_login_password.py

# Cloud: browser-and-paste login fallback (MFA accounts)
python examples/cloud_login.py

# Local: read appliance state
python examples/basic_usage.py 192.168.1.100 123456

# Local: trigger PIN display (door must be open)
python examples/get_pin.py 192.168.1.100

# Local: real-time push updates
python examples/push_demo.py 192.168.1.100 123456

# Cloud: list and read all appliances
python examples/cloud_usage.py

# Cloud: real-time push updates for all appliances
python examples/cloud_push_demo.py
```
