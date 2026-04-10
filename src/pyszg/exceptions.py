"""pyszg exceptions."""


class SZGError(Exception):
    """Base exception for pyszg."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class SZGConnectionError(SZGError):
    """Failed to connect to the appliance module."""


class AuthenticationError(SZGError):
    """PIN authentication failed."""


class CommandError(SZGError):
    """Appliance rejected the command."""


# Alias for backward compatibility
ConnectionError = SZGConnectionError
