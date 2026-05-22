"""pyszg exceptions.

Library callers (notably the szg-hass integration) catch these to
classify failures into Home Assistant error types:

- AuthenticationError  -> ConfigEntryAuthFailed (triggers reauth flow)
- SZGConnectionError   -> ConfigEntryNotReady / UpdateFailed
- SZGTimeoutError      -> ConfigEntryNotReady / UpdateFailed
- CommandError         -> entity unavailable / log warning
"""


class SZGError(Exception):
    """Base exception for pyszg."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SZGConnectionError(SZGError):
    """Failed to connect to the appliance module or cloud endpoint."""


class SZGTimeoutError(SZGError):
    """A request timed out waiting for a response."""


class AuthenticationError(SZGError):
    """PIN authentication failed, or cloud OAuth tokens are invalid."""


class CommandError(SZGError):
    """Appliance or cloud rejected the command."""
