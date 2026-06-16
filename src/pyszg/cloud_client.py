"""Cloud API client for Sub-Zero Group appliances."""

from __future__ import annotations

import json
import logging
import socket
import ssl
import uuid
import urllib.request
import urllib.error
from typing import Any

from .appliance import Appliance, ModuleGeneration
from .cloud_auth import TokenSet, TokenStore
from .cloud_const import API_BASE, SUBSCRIPTION_KEY
from .exceptions import (
    SZGConnectionError,
    SZGTimeoutError,
    AuthenticationError,
    CommandError,
)

_LOGGER = logging.getLogger(__name__)


class SZGCloudClient:
    """Client for the Sub-Zero Group cloud API.

    Provides access to all appliances on the account, including newer
    NGIX/Saber modules that don't support direct IP control.

    Construct via a shared ``TokenStore`` so cloud REST and SignalR
    refresh in lockstep and rotated refresh tokens are persisted via
    the store's ``on_refresh`` callback::

        store = TokenStore(tokens, auth, on_refresh=save_to_disk)
        client = SZGCloudClient(store)
        signalr = SZGCloudSignalR(store)

    Usage::

        from pyszg import SZGCloudAuth, SZGCloudClient, TokenStore

        auth = SZGCloudAuth()
        # First time: see examples/cloud_login.py for the interactive
        # browser-paste flow that produces a TokenSet.
        tokens = auth.load_tokens("cloud_tokens.json")
        store = TokenStore(tokens, auth)

        client = SZGCloudClient(store)
        devices = client.get_devices()
        for dev in devices:
            print(dev["id"], dev["applianceId"])

        # Get appliance state
        appliance = client.get_appliance_state("00068002fc90")

        # Control appliance
        client.send_command("00068002fc90", "set", {"ref_set_temp": 37})
    """

    def __init__(self, store: TokenStore):
        self._store = store
        self._ssl_context = ssl.create_default_context()

    @property
    def tokens(self) -> TokenSet:
        """Current tokens. Read this if you need the live id_token or
        user_id; the store handles refresh and rotation automatically.
        """
        return self._store.tokens

    @property
    def token_store(self) -> TokenStore:
        """The shared token store. Pass this to ``SZGCloudSignalR`` so
        both clients refresh the same tokens.
        """
        return self._store

    @property
    def user_id(self) -> str:
        return self._store.tokens.user_id

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated API request.

        On a 401 with a token the store still considers valid, force one
        refresh and retry before surfacing ``AuthenticationError``. This
        absorbs clock-skew / rotation blips that would otherwise escalate
        a single transient 401 into a spurious Home Assistant reauth flow.
        If the forced refresh fails, or the retry still returns 401, the
        ``AuthenticationError`` propagates as a genuine auth failure.
        """
        tokens = self._store.get_valid()
        try:
            return self._send(method, path, data, tokens)
        except AuthenticationError:
            tokens = self._store.force_refresh(stale=tokens)
            return self._send(method, path, data, tokens)

    def _send(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None,
        tokens: TokenSet,
    ) -> Any:
        """Issue a single authenticated request with the given tokens."""
        url = f"{API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {tokens.id_token}",
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
            "Content-Type": "application/json",
            "Userid": tokens.user_id,
        }

        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            resp = urllib.request.urlopen(req, timeout=15, context=self._ssl_context)
            raw = resp.read().decode()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                # CAT modules may return plain strings like "OK"
                return {"_raw": raw}
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                err_body = e.read().decode()
                err_json = json.loads(err_body)
                msg = err_json.get("Message", err_json.get("message", str(err_json)))
            except Exception:
                msg = f"HTTP {status}"

            if status == 401:
                raise AuthenticationError(msg, status=status)
            if status == 500 and msg == "OK":
                # CAT modules return "OK" via cloud direct method
                # which the API gateway wraps as a 500 error
                return {"_raw": "OK"}
            raise CommandError(msg, status=status)
        except socket.timeout as exc:
            raise SZGTimeoutError(f"Request to {path} timed out") from exc
        except urllib.error.URLError as exc:
            # Wraps connection refused, DNS failure, and (on some Python
            # versions) socket.timeout. We've already handled the timeout
            # case above; everything left here is a transport failure.
            if isinstance(exc.reason, socket.timeout):
                raise SZGTimeoutError(f"Request to {path} timed out") from exc
            raise SZGConnectionError(f"Cannot reach {API_BASE}: {exc.reason}") from exc

    def get_devices(self) -> list[dict[str, Any]]:
        """List all appliances on the account.

        Returns a list of device dicts with keys:
        id, macId, applianceId, temperatureUnitForAppliance,
        name, bda, serialNumber, etc.
        """
        resp = self._request("GET", "/consumerapp/user/devices")
        return resp.get("devices", [])

    def send_command(
        self,
        device_id: str,
        cmd: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a command to an appliance via cloud direct method.

        Args:
            device_id: Device ID (MAC-based for CAT, hash for NGIX/Saber).
            cmd: Command name (e.g., "set", "get_async", "open_cloud_async").
            params: Optional command parameters.

        Returns:
            Response dict from the appliance.
        """
        payload: dict[str, Any] = {
            "req_id": str(uuid.uuid4()),
            "pload": {"cmd": cmd},
        }
        if params:
            payload["pload"]["params"] = params

        return self._request(
            "POST",
            f"/consumerapp/device/{device_id}/directmethod/executeAPICmd",
            data=payload,
        )

    def get_appliance_state(
        self,
        device_id: str,
        module_generation: ModuleGeneration | None = None,
    ) -> Appliance:
        """Get the current state of an appliance via cloud.

        Returns a fresh Appliance object with parsed state. Callers that
        need to merge deltas across calls should hold their own Appliance
        instance and call ``update_from_response`` on it; this client is
        stateless.

        Uses 'get' for CAT modules and 'get_async' for Saber/NGIX modules
        (which respond differently). Pass ``module_generation`` (derivable
        from the ``applianceId`` the caller already holds) to skip the
        wasted 'get' probe on Saber/NGIX devices, which only answer
        'get_async' — otherwise every poll spends an extra round trip
        getting the CAT "OK" wrapper before falling back.
        """
        appliance = Appliance()

        if module_generation == ModuleGeneration.SABER:
            # Saber/NGIX only answer get_async — go straight there.
            resp = self.send_command(device_id, "get_async")
        else:
            # Try 'get' first (works for CAT); fall back to get_async when
            # the module returns the "OK" wrapper instead of state.
            resp = self.send_command(device_id, "get")
            if "_raw" in resp:
                resp = self.send_command(device_id, "get_async")

        if "_raw" in resp:
            return appliance

        # Properties may be at top level or under "resp"
        props = resp.get("resp", resp)
        if "appliance_type" in props or "uptime" in props or "notifs" in props:
            appliance.update_from_response(props)

        return appliance

    def set_property(
        self,
        device_id: str,
        name: str,
        value: Any,
    ) -> dict[str, Any]:
        """Set a property on an appliance via cloud.

        Args:
            device_id: Device ID.
            name: Property key (e.g., "ref_set_temp", "cav_light_on").
            value: Property value.
        """
        return self.send_command(device_id, "set", {name: value})

    def open_cloud_async(self, device_id: str) -> dict[str, Any]:
        """Open the cloud async channel for an appliance.

        This tells the appliance to start sending updates via SignalR.
        """
        return self.send_command(device_id, "open_cloud_async")
