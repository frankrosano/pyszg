"""SignalR real-time push updates from the Sub-Zero Group cloud."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
import ssl
import time
import uuid
import urllib.request
import urllib.error
from typing import Any, Callable

import websockets

from .cloud_auth import SZGCloudAuth, TokenSet
from .cloud_const import API_BASE, SUBSCRIPTION_KEY
from .exceptions import (
    SZGConnectionError,
    SZGTimeoutError,
    AuthenticationError,
)

_LOGGER = logging.getLogger(__name__)

RECORD_SEP = "\x1e"

# Reconnect 5 minutes before the SignalR token expires
_TOKEN_REFRESH_MARGIN = 300


def _get_token_expiry(token: str) -> float:
    """Extract the exp claim from a JWT. Returns 0 on failure."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims.get("exp", 0))
    except Exception:
        return 0


def _parse_signalr_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the triple-nested SignalR ConnectedApplianceMessage.

    Returns:
        {
            "device_id": str,
            "msg_type": int,  # 1 = full state, 2 = delta
            "data": dict,     # properties (full state or delta props)
        }
        Or None if the message isn't a ConnectedApplianceMessage.
    """
    if msg.get("type") != 1 or msg.get("target") != "ConnectedApplianceMessage":
        return None

    args = msg.get("arguments", [])
    if not args:
        return None

    try:
        outer = json.loads(args[0])
        device_id = outer.get("DeviceId", "")
        payload = json.loads(outer.get("Payload", "{}"))
        channel = json.loads(payload.get("api.async_channel", "{}"))
        return {
            "device_id": device_id,
            "msg_type": channel.get("type", 0),
            "data": channel.get("pload", {}),
        }
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        _LOGGER.debug("Failed to parse SignalR message: %s", exc)
        return None


class SZGCloudSignalR:
    """Real-time push updates via Azure SignalR Service.

    Connects to the Sub-Zero cloud SignalR hub and receives instant
    state change notifications for all appliances on the account.

    Usage:
        from pyszg import SZGCloudAuth, SZGCloudSignalR

        auth = SZGCloudAuth()
        tokens = auth.load_tokens("tokens.json")
        tokens = auth.ensure_valid(tokens)

        signalr = SZGCloudSignalR(tokens, auth)

        async def on_update(device_id, msg_type, data):
            if msg_type == 2:  # delta
                props = data.get("props", data)
                print(f"{device_id}: {props}")

        await signalr.connect(callback=on_update)
    """

    def __init__(self, tokens: TokenSet, auth: SZGCloudAuth | None = None):
        self._tokens = tokens
        self._auth = auth or SZGCloudAuth()
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._token_expires_at: float = 0.0

    @property
    def is_connected(self) -> bool:
        """True iff the WebSocket is currently open and the SignalR access
        token has not yet expired.

        After the token expires Azure stops routing appliance messages
        even though the WebSocket itself stays open, so this property
        returns False once the cached expiry is reached.
        """
        if self._ws is None or self._ws.closed:
            return False
        if self._token_expires_at and time.time() >= self._token_expires_at:
            return False
        return True

    def _ensure_auth(self) -> None:
        if self._tokens.is_expired:
            self._tokens = self._auth.refresh(self._tokens)

    def _api_request(self, method: str, path: str, data: Any = None,
                     lowercase_userid: bool = False) -> Any:
        """Make an authenticated API request. Must be called from executor thread."""
        self._ensure_auth()
        url = f"{API_BASE}{path}"
        uid = self._tokens.user_id.lower() if lowercase_userid else self._tokens.user_id
        headers = {
            "Authorization": f"Bearer {self._tokens.id_token}",
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
            "Content-Type": "application/json",
            "Userid": uid,
        }
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        ctx = ssl.create_default_context()
        try:
            resp = urllib.request.urlopen(req, timeout=15, context=ctx)
            return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise AuthenticationError(
                    f"Cloud auth rejected during {path}: HTTP 401",
                    status=exc.code,
                ) from exc
            raise SZGConnectionError(
                f"{method} {path} failed: HTTP {exc.code}",
                status=exc.code,
            ) from exc
        except socket.timeout as exc:
            raise SZGTimeoutError(f"{method} {path} timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise SZGTimeoutError(f"{method} {path} timed out") from exc
            raise SZGConnectionError(f"{method} {path} failed: {exc.reason}") from exc

    def _negotiate(self) -> dict[str, str]:
        """Get SignalR connection URL and access token."""
        return self._api_request("POST", "/signal-r/negotiateUser", lowercase_userid=True)

    def _open_cloud_async(self, device_id: str) -> None:
        """Tell a device to start pushing updates to SignalR."""
        payload = {"req_id": str(uuid.uuid4()), "pload": {"cmd": "open_cloud_async"}}
        try:
            self._api_request(
                "POST",
                f"/consumerapp/device/{device_id}/directmethod/executeAPICmd",
                data=payload,
            )
            _LOGGER.debug("open_cloud_async succeeded for %s", device_id)
        except AuthenticationError:
            # Don't swallow auth errors — let the outer connect() loop
            # surface them so the integration can trigger reauth.
            raise
        except (SZGConnectionError, SZGTimeoutError) as exc:
            # CAT modules return 500 with "OK" which our HTTPError branch
            # turns into SZGConnectionError; that's normal here. Saber/NGIX
            # devices may legitimately fail (offline) — log at debug so we
            # can still diagnose if needed without spamming warnings.
            _LOGGER.debug("open_cloud_async for %s: %s", device_id, exc)

    async def connect(
        self,
        device_ids: list[str] | None = None,
        callback: Callable[[str, int, dict[str, Any]], Any] | None = None,
    ) -> None:
        """Connect to SignalR and listen for updates indefinitely.

        Args:
            device_ids: List of device IDs to subscribe to. If None,
                       opens async channels for all devices on the account.
            callback: Called for each update with (device_id, msg_type, data).
                     msg_type 1 = full state, 2 = delta update.
                     For deltas, data contains {"props": {"key": value}}.
                     The library is stateless — callers retain their
                     own Appliance instance and merge via
                     ``Appliance.update_from_response``.
        """
        self._running = True
        retry_delay = 5
        max_delay = 300  # 5 minutes max
        consecutive_failures = 0

        while self._running:
            try:
                consecutive_failures = 0
                retry_delay = 5  # Reset on successful connection
                await self._connect_and_listen(device_ids, callback)
            except AuthenticationError:
                # Auth failures are not recoverable by reconnecting — the
                # caller (e.g. the HA integration) needs to drive a
                # reauth flow. Re-raise to surface the condition.
                self._running = False
                raise
            except (websockets.exceptions.ConnectionClosed, SZGConnectionError, SZGTimeoutError) as exc:
                consecutive_failures += 1
                _LOGGER.warning(
                    "SignalR connection lost (attempt %d): %s. Reconnecting in %ds...",
                    consecutive_failures, exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            except Exception as exc:
                consecutive_failures += 1
                _LOGGER.exception(
                    "Unexpected SignalR error (attempt %d). Reconnecting in %ds...",
                    consecutive_failures, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    async def _connect_and_listen(
        self,
        device_ids: list[str] | None,
        callback: Callable | None,
    ) -> None:
        """Single connection attempt. Returns when the token is about to expire."""
        loop = asyncio.get_event_loop()

        # Run blocking negotiate in executor
        info = await loop.run_in_executor(None, self._negotiate)
        ws_url = info["url"].replace("https://", "wss://") + "&access_token=" + info["accessToken"]

        # Determine when this connection's token expires.
        # The SignalR access token (1h lifetime) is separate from the OAuth
        # id_token. Once it expires, Azure stops routing messages but keeps
        # the WebSocket alive for pings — so we must reconnect before expiry.
        token_exp = _get_token_expiry(info["accessToken"])
        if token_exp:
            self._token_expires_at = token_exp
            reconnect_at = token_exp - _TOKEN_REFRESH_MARGIN
            ttl = int(token_exp - time.time())
            _LOGGER.debug("SignalR token expires in %ds, will reconnect in %ds", ttl, ttl - _TOKEN_REFRESH_MARGIN)
        else:
            # Fallback: reconnect every 50 minutes if we can't read the token
            reconnect_at = time.time() + 3000
            self._token_expires_at = reconnect_at + _TOKEN_REFRESH_MARGIN
            _LOGGER.debug("Could not read SignalR token expiry, will reconnect in 50min")

        # Pre-create SSL context in executor to avoid blocking the event loop
        ssl_context = await loop.run_in_executor(None, ssl.create_default_context)

        _LOGGER.info("Connecting to SignalR...")
        async with websockets.connect(
            ws_url,
            ssl=ssl_context,
            ping_interval=20,
            ping_timeout=10,
        ) as ws:
            self._ws = ws

            # Handshake
            await ws.send(json.dumps({"protocol": "json", "version": 1}) + RECORD_SEP)
            await asyncio.wait_for(ws.recv(), timeout=10)
            _LOGGER.info("SignalR connected")

            # Open async channels (blocking HTTP calls, run in executor)
            ids_to_open = device_ids
            if not ids_to_open:
                try:
                    resp = await loop.run_in_executor(
                        None, self._api_request, "GET", "/consumerapp/user/devices"
                    )
                    ids_to_open = [dev["id"] for dev in resp.get("devices", [])]
                except Exception as exc:
                    _LOGGER.warning("Failed to get device list: %s", exc)
                    ids_to_open = []

            for dev_id in ids_to_open:
                await loop.run_in_executor(None, self._open_cloud_async, dev_id)

            # Listen loop — exits when token is about to expire
            while self._running:
                if time.time() >= reconnect_at:
                    _LOGGER.debug("SignalR token expiring soon, reconnecting with fresh token")
                    return  # Clean exit — outer loop will reconnect

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    continue

                for part in raw.split(RECORD_SEP):
                    part = part.strip()
                    if not part:
                        continue
                    try:
                        msg = json.loads(part)
                    except json.JSONDecodeError:
                        continue

                    # Respond to pings
                    if msg.get("type") == 6:
                        await ws.send(json.dumps({"type": 6}) + RECORD_SEP)
                        continue

                    # Parse appliance message
                    parsed = _parse_signalr_message(msg)
                    if not parsed:
                        continue

                    device_id = parsed["device_id"]
                    msg_type = parsed["msg_type"]
                    data = parsed["data"]

                    # Notify callback. The library does not retain
                    # appliance state; the caller (e.g. the HA
                    # coordinator) is responsible for routing the
                    # delta into its own Appliance instance.
                    if callback:
                        try:
                            result = callback(device_id, msg_type, data)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            _LOGGER.exception(
                                "Callback error in SignalR handler"
                            )

    async def disconnect(self) -> None:
        """Disconnect from SignalR."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
