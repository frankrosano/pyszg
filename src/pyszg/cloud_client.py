"""Cloud API client for Sub-Zero Group appliances."""

from __future__ import annotations

import json
import logging
import ssl
import uuid
import urllib.request
import urllib.error
from typing import Any

from .appliance import Appliance
from .cloud_auth import SZGCloudAuth, TokenSet
from .cloud_const import API_BASE, SUBSCRIPTION_KEY
from .exceptions import SZGError, AuthenticationError, CommandError

_LOGGER = logging.getLogger(__name__)


class SZGCloudClient:
    """Client for the Sub-Zero Group cloud API.

    Provides access to all appliances on the account, including newer
    NGIX/Saber modules that don't support direct IP control.

    Usage:
        from pyszg import SZGCloudAuth, SZGCloudClient

        auth = SZGCloudAuth()
        tokens = auth.login()  # first time — opens browser

        client = SZGCloudClient(tokens)
        devices = client.get_devices()
        for dev in devices:
            print(dev["id"], dev["applianceId"])

        # Get appliance state
        appliance = client.get_appliance_state("00068002fc90")

        # Control appliance
        client.send_command("00068002fc90", "set", {"ref_set_temp": 37})
    """

    def __init__(self, tokens: TokenSet, auth: SZGCloudAuth | None = None):
        self._tokens = tokens
        self._auth = auth or SZGCloudAuth()
        self._appliances: dict[str, Appliance] = {}
        self._ssl_context = ssl.create_default_context()

    @property
    def tokens(self) -> TokenSet:
        return self._tokens

    @property
    def user_id(self) -> str:
        return self._tokens.user_id

    def _ensure_auth(self) -> None:
        """Refresh tokens if expired."""
        if self._tokens.is_expired:
            _LOGGER.info("Token expired, refreshing")
            self._tokens = self._auth.refresh(self._tokens)

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated API request."""
        self._ensure_auth()

        url = f"{API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self._tokens.id_token}",
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
            "Content-Type": "application/json",
            "Userid": self._tokens.user_id,
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
        except (AuthenticationError, CommandError):
            raise
        except Exception as exc:
            raise SZGError(f"API request failed: {exc}")

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

    def get_appliance_state(self, device_id: str) -> Appliance:
        """Get the current state of an appliance via cloud.

        Returns an Appliance object with parsed state, same as
        the local SZGClient.refresh() method.

        Uses 'get' command for CAT modules and 'get_async' for
        Saber/NGIX modules (which respond differently).
        """
        # Try 'get' first (works for all module types)
        resp = self.send_command(device_id, "get")

        if device_id not in self._appliances:
            self._appliances[device_id] = Appliance()

        appliance = self._appliances[device_id]

        # CAT modules return "OK" wrapper for some commands
        if "_raw" in resp:
            # Fall back to get_async which works for Saber/NGIX
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
