"""High-level client for Sub-Zero Group connected appliances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .appliance import Appliance
from .connection import CATConnection, CATStreamConnection, DEFAULT_PORT

_LOGGER = logging.getLogger(__name__)


class SZGClient:
    """Client for communicating with a Sub-Zero Group appliance over local IP.

    Usage:
        # Read-only (no PIN needed):
        client = SZGClient("10.105.5.50")
        client.refresh()
        print(client.appliance.cavity1.temp)

        # Full access:
        client = SZGClient("10.105.5.50", pin="635412")
        client.refresh()
        client.set_property("cav_light_on", True)

        # Polling:
        client.start_polling(interval=5, callback=on_change)
        ...
        client.stop_polling()
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT, pin: str | None = None):
        self._host = host
        self._port = port
        self._pin = pin
        self._conn = CATConnection(host, port)
        self._appliance = Appliance(host=host)
        self._stream: CATStreamConnection | None = None
        self._polling_task: asyncio.Task | None = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def pin(self) -> str | None:
        return self._pin

    @pin.setter
    def pin(self, value: str | None) -> None:
        self._pin = value

    @property
    def appliance(self) -> Appliance:
        return self._appliance

    def refresh(self) -> Appliance:
        """Fetch current state from the appliance.

        If a PIN is configured, performs an authenticated request
        which returns the full property set. Without a PIN, returns
        a minimal set (door state, unit on/off, model, uptime).
        """
        resp = self._conn.execute({"cmd": "get_async"}, pin=self._pin)
        self._appliance.update_from_response(resp)
        return self._appliance

    def refresh_minimal(self) -> Appliance:
        """Fetch minimal state without authentication (no PIN needed)."""
        resp = self._conn.execute_unauthenticated({"cmd": "get_async"})
        self._appliance.update_from_response(resp)
        return self._appliance

    def set_property(self, name: str, value: Any) -> dict[str, Any]:
        """Set a property on the appliance. Requires PIN.

        Args:
            name: Property key (e.g., "cav_light_on", "ref_set_temp").
            value: Property value (bool, int, str depending on property).

        Returns:
            Response dict from the appliance.
        """
        if not self._pin:
            raise ValueError("PIN required for write commands. Set client.pin first.")
        return self._conn.execute(
            {"cmd": "set", "params": {name: value}},
            pin=self._pin,
        )

    def display_pin(self, duration: int = 20) -> dict[str, Any]:
        """Request the appliance to display its PIN.

        A door on the appliance must be physically open for this to work.
        Duration is in seconds (max ~30).
        """
        return self._conn.execute_unauthenticated(
            {"cmd": "display_pin", "params": {"duration": duration}}
        )

    def unlock(self, pin: str) -> bool:
        """Test if a PIN is valid. Returns True if accepted.

        Also stores the PIN for future authenticated requests.
        """
        resp = self._conn.execute(
            {"cmd": "unlock_channel", "params": {"pin": pin}},
        )
        # unlock is a single-command operation (no prior unlock needed)
        # If we get here without exception, the PIN is valid
        self._pin = pin
        return True

    def scan_wifi(self) -> list[dict[str, Any]]:
        """Scan for WiFi networks visible to the appliance module."""
        resp = self._conn.execute_unauthenticated({"cmd": "scan"})
        return resp.get("aps", [])

    # --- Polling ---

    async def poll_async(
        self,
        interval: float = 5.0,
        callback: Callable[[Appliance], None] | None = None,
    ) -> None:
        """Continuously poll the appliance for state changes.

        Args:
            interval: Seconds between polls.
            callback: Called with the Appliance instance whenever state changes.
        """
        previous_raw: dict[str, Any] = {}

        while True:
            try:
                self.refresh()
                current_raw = self._appliance.raw

                if current_raw != previous_raw and callback:
                    callback(self._appliance)

                previous_raw = current_raw.copy()
            except Exception as exc:
                _LOGGER.warning("Poll failed: %s", exc)

            await asyncio.sleep(interval)

    def start_polling(
        self,
        interval: float = 5.0,
        callback: Callable[[Appliance], None] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Start polling in the background."""
        if self._polling_task and not self._polling_task.done():
            return

        _loop = loop or asyncio.get_event_loop()
        self._polling_task = _loop.create_task(
            self.poll_async(interval=interval, callback=callback)
        )

    def stop_polling(self) -> None:
        """Stop background polling."""
        if self._polling_task:
            self._polling_task.cancel()
            self._polling_task = None

    # --- Push (persistent connection) ---

    def connect_push(self) -> dict[str, Any]:
        """Open a persistent push connection for real-time updates.

        Requires PIN. Returns the initial state snapshot.
        After calling this, use read_update() or iterate with
        for update in client.push_updates(): ...

        Returns:
            Initial full state dict.
        """
        if not self._pin:
            raise ValueError("PIN required for push connection. Set client.pin first.")
        self._stream = CATStreamConnection(self._host, self._port, self._pin)
        initial = self._stream.connect()
        self._appliance.update_from_response(initial)
        return initial

    def read_update(self, timeout: float = 30.0) -> dict[str, Any] | None:
        """Read the next push update from the persistent connection.

        Returns a delta dict like {"props": {"cav_door_ajar": true}},
        or None on timeout. Also updates the appliance state automatically.
        """
        if not self._stream or not self._stream.connected:
            raise ConnectionError("No push connection. Call connect_push() first.")
        update = self._stream.read_update(timeout=timeout)
        if update and "props" in update:
            # Apply delta to the appliance state
            self._appliance.update_from_response(update["props"])
        return update

    def push_updates(self):
        """Iterate over push updates indefinitely.

        Yields delta update dicts. Also updates appliance state automatically.

        Usage:
            client.connect_push()
            for update in client.push_updates():
                print(update["props"])
                print(client.appliance.cavity1.door_ajar)
        """
        if not self._stream:
            raise ConnectionError("No push connection. Call connect_push() first.")
        for update in self._stream:
            if "props" in update:
                self._appliance.update_from_response(update["props"])
            yield update

    def disconnect_push(self) -> None:
        """Close the persistent push connection."""
        if self._stream:
            self._stream.close()
            self._stream = None
