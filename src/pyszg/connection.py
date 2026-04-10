"""Low-level TLS connection to the Connected Appliance Module (CAT)."""

from __future__ import annotations

import json
import logging
import ssl
import socket
import time
from typing import Any

from .exceptions import SZGConnectionError as ConnectionError, AuthenticationError, CommandError

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 10
DEFAULT_TIMEOUT = 10

# Status codes returned by the CAT module
STATUS_OK = 0
STATUS_OUT_OF_RANGE = 3
STATUS_UNKNOWN_COMMAND = 5
STATUS_BAD_FORMAT = 6
STATUS_APPLIANCE_NAK = 101


def _create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that accepts the module's self-signed cert.

    The CAT module uses a self-signed certificate that cannot be verified
    against any CA. This is expected for local appliance communication.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class CATConnection:
    """A single authenticated session to the CAT module.

    The module supports two modes:

    1. Request/response: unlock_channel + one command, then close.
       Used for set_property, display_pin, etc.

    2. Persistent push: unlock_channel + get_async keeps the connection
       open. The module pushes delta updates as JSON lines whenever
       appliance state changes (door open, temp change, timer, etc.).

    Push message format:
        {"msg_types": 2, "seq": N, "timestamp": "ISO8601", "props": {"key": value}}
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: ssl.SSLSocket | None = None
        self._authenticated = False

    def _connect(self) -> ssl.SSLSocket:
        """Establish a new TLS connection."""
        ctx = _create_ssl_context()
        try:
            raw = socket.create_connection((self._host, self._port), timeout=self._timeout)
            sock = ctx.wrap_socket(raw, server_hostname=self._host)
            sock.settimeout(self._timeout)
            return sock
        except OSError as exc:
            raise ConnectionError(f"Failed to connect to {self._host}:{self._port}: {exc}") from exc

    def _send_command(self, sock: ssl.SSLSocket, cmd: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON command and return the parsed response."""
        payload = json.dumps(cmd, separators=(",", ":")).encode("utf-8") + b"\n"
        _LOGGER.debug("Sending to %s: %s", self._host, cmd)
        try:
            sock.sendall(payload)
            data = sock.recv(16384)
            if not data:
                raise ConnectionError("Connection closed by appliance")
            decoded = data.decode("utf-8").strip()
            response = json.loads(decoded)
            _LOGGER.debug("Received from %s: status=%s", self._host, response.get("status"))
            return response
        except (OSError, json.JSONDecodeError) as exc:
            raise ConnectionError(f"Communication error: {exc}") from exc

    def _check_response(self, response: dict[str, Any], context: str = "") -> dict[str, Any]:
        """Check response status and raise appropriate exceptions."""
        status = response.get("status")
        if status == STATUS_OK:
            return response.get("resp", {})

        msg = response.get("status_msg", f"Status {status}")
        if context:
            msg = f"{context}: {msg}"

        if status == STATUS_OUT_OF_RANGE:
            lockout = response.get("resp", {}).get("lockout_duration")
            if lockout:
                raise AuthenticationError(f"PIN rejected (lockout {lockout}s)", status=status)
            raise CommandError(msg, status=status)
        elif status == STATUS_UNKNOWN_COMMAND:
            raise CommandError(msg, status=status)
        elif status == STATUS_BAD_FORMAT:
            raise CommandError(msg, status=status)
        elif status == STATUS_APPLIANCE_NAK:
            raise CommandError(msg, status=status)
        else:
            raise CommandError(msg, status=status)

    def execute(self, cmd: dict[str, Any], pin: str | None = None) -> dict[str, Any]:
        """Execute a command, optionally authenticating first.

        Opens a fresh TLS connection, optionally sends unlock_channel,
        then sends the command, and closes the connection.
        """
        if pin is not None and (len(pin) != 6 or not pin.isdigit()):
            raise ValueError("PIN must be exactly 6 digits")
        sock = self._connect()
        try:
            if pin:
                unlock_resp = self._send_command(
                    sock, {"cmd": "unlock_channel", "params": {"pin": pin}}
                )
                self._check_response(unlock_resp, "unlock_channel")

            response = self._send_command(sock, cmd)
            return self._check_response(response)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def execute_unauthenticated(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Execute a command without PIN authentication."""
        return self.execute(cmd, pin=None)


class CATStreamConnection:
    """Persistent push connection to the CAT module.

    Opens an authenticated TLS connection, sends get_async to get the
    initial state snapshot, then keeps the connection alive to receive
    real-time delta updates as newline-delimited JSON.

    Usage:
        stream = CATStreamConnection("10.105.5.50", pin="635412")
        initial_state = stream.connect()

        # Blocking iteration over push updates:
        for update in stream:
            print(update)  # {"msg_types": 2, "seq": N, "props": {...}}

        stream.close()
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT, pin: str = "",
                 timeout: float = DEFAULT_TIMEOUT):
        self._host = host
        self._port = port
        self._pin = pin
        self._timeout = timeout
        self._sock: ssl.SSLSocket | None = None
        self._buffer = b""

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> dict[str, Any]:
        """Connect, authenticate, and return the initial state snapshot.

        Returns:
            The full appliance state dict (same as get_async response).

        Raises:
            ConnectionError: If the connection fails.
            AuthenticationError: If the PIN is rejected.
        """
        if not self._pin:
            raise ValueError("PIN required for push connection.")

        ctx = _create_ssl_context()
        try:
            raw = socket.create_connection((self._host, self._port), timeout=self._timeout)
            self._sock = ctx.wrap_socket(raw, server_hostname=self._host)
            self._sock.settimeout(self._timeout)
        except OSError as exc:
            raise ConnectionError(f"Failed to connect to {self._host}:{self._port}: {exc}") from exc

        # Authenticate
        unlock_payload = json.dumps(
            {"cmd": "unlock_channel", "params": {"pin": self._pin}},
            separators=(",", ":"),
        ).encode() + b"\n"
        self._sock.sendall(unlock_payload)

        unlock_data = self._read_json_line()
        if unlock_data.get("status") != STATUS_OK:
            self.close()
            lockout = unlock_data.get("resp", {}).get("lockout_duration")
            if lockout:
                raise AuthenticationError(f"PIN rejected (lockout {lockout}s)")
            raise AuthenticationError(
                unlock_data.get("status_msg", f"Unlock failed: status {unlock_data.get('status')}")
            )

        # Send get_async to get initial snapshot and open push channel
        get_payload = json.dumps({"cmd": "get_async"}, separators=(",", ":")).encode() + b"\n"
        self._sock.sendall(get_payload)

        initial = self._read_json_line()
        if initial.get("status") != STATUS_OK:
            self.close()
            raise ConnectionError(
                initial.get("status_msg", f"get_async failed: status {initial.get('status')}")
            )

        # Switch to non-blocking reads for the push stream
        self._sock.settimeout(1.0)

        _LOGGER.info("Push connection established to %s", self._host)
        return initial.get("resp", {})

    def read_update(self, timeout: float = 30.0) -> dict[str, Any] | None:
        """Read the next push update from the connection.

        Args:
            timeout: Max seconds to wait for an update.

        Returns:
            A delta update dict like:
            {"msg_types": 2, "seq": N, "timestamp": "...", "props": {"key": val}}
            Or None if timeout elapsed with no update.

        Raises:
            ConnectionError: If the connection is lost.
        """
        if not self._sock:
            raise ConnectionError("Not connected")

        deadline = time.time() + timeout
        while time.time() < deadline:
            # Check if we have a complete line in the buffer
            if b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                try:
                    return json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    _LOGGER.warning("Invalid JSON in push update: %s", line[:100])
                    continue

            # Read more data
            try:
                chunk = self._sock.recv(8192)
                if not chunk:
                    self.close()
                    raise ConnectionError("Connection closed by appliance")
                self._buffer += chunk
            except socket.timeout:
                continue
            except ssl.SSLError as e:
                if "WANT_READ" in str(e):
                    continue
                self.close()
                raise ConnectionError(f"SSL error: {e}") from e
            except OSError as e:
                self.close()
                raise ConnectionError(f"Connection lost: {e}") from e

        return None  # Timeout

    def __iter__(self):
        """Iterate over push updates indefinitely."""
        while self.connected:
            update = self.read_update(timeout=60.0)
            if update is not None:
                yield update

    def close(self) -> None:
        """Close the connection."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            _LOGGER.info("Push connection closed to %s", self._host)

    def _read_json_line(self) -> dict[str, Any]:
        """Read a single JSON response line (used during handshake)."""
        data = b""
        self._sock.settimeout(self._timeout)
        while True:
            try:
                chunk = self._sock.recv(8192)
                if not chunk:
                    raise ConnectionError("Connection closed during handshake")
                data += chunk
                if b"\n" in data:
                    line = data.split(b"\n", 1)[0]
                    # Store any remaining data in buffer
                    self._buffer = data.split(b"\n", 1)[1]
                    return json.loads(line.decode("utf-8"))
            except socket.timeout:
                raise ConnectionError("Timeout waiting for response")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
